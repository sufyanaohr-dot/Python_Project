"""Billing engine, transaction persistence, GST calculation, and PDF invoice export."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from checkout import DiscountEngine, ShoppingCart
from database import BASE_DIR, DatabaseManager
from inventory import InventoryManager
from models import (
    DEFAULT_GST_RATE,
    EmptyCartError,
    InvalidPriceError,
    Invoice,
    InvoiceLine,
)


DEFAULT_INVOICES_DIR = BASE_DIR / "invoices"


def _escape_pdf_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _write_minimal_pdf(lines: list[str], output_path: Path) -> None:
    """Pure-Python PDF 1.4 fallback writer used if ReportLab is not installed."""
    page_lines = lines[:52]
    commands = ["BT", "/F1 10 Tf", "45 770 Td", "14 TL"]
    for idx, raw_line in enumerate(page_lines):
        safe_line = _escape_pdf_text(raw_line.encode("latin-1", "replace").decode("latin-1"))
        if idx == 0:
            commands.append(f"({safe_line}) Tj")
        else:
            commands.append(f"T* ({safe_line}) Tj")
    commands.append("ET")
    stream_bytes = "\n".join(commands).encode("latin-1")

    objects: list[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
        ),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj\n",
        (
            f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
            + stream_bytes
            + b"\nendstream\nendobj\n"
        ),
    ]

    pdf_chunks: list[bytes] = [b"%PDF-1.4\n"]
    offsets: list[int] = [0]
    for obj in objects:
        offsets.append(sum(len(chunk) for chunk in pdf_chunks))
        pdf_chunks.append(obj)

    xref_offset = sum(len(chunk) for chunk in pdf_chunks)
    xref_lines = [f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")]
    for off in offsets[1:]:
        xref_lines.append(f"{off:010d} 00000 n \n".encode("latin-1"))

    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")

    output_path.write_bytes(b"".join(pdf_chunks + xref_lines + [trailer]))


class BillingManager:
    """Processes checkouts atomically, computes GST/discounts, and exports PDF invoices."""

    def __init__(
        self,
        db: DatabaseManager,
        inventory: InventoryManager,
        gst_rate: float = DEFAULT_GST_RATE,
        invoices_dir: str | Path = DEFAULT_INVOICES_DIR,
    ) -> None:
        if gst_rate < 0 or gst_rate > 1:
            raise InvalidPriceError(f"GST rate must be between 0.0 and 1.0, got {gst_rate!r}.")
        self.db = db
        self.inventory = inventory
        self.discount_engine = DiscountEngine(db)
        self.gst_rate = float(gst_rate)
        self.invoices_dir = Path(invoices_dir)
        self.invoices_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def generate_bill_reference() -> str:
        """Create a unique, human-readable invoice reference ID."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = secrets.token_hex(2).upper()
        return f"INV-{stamp}-{suffix}"

    def preview_totals(
        self,
        cart: ShoppingCart,
        promo_code: Optional[str] = None,
    ) -> dict[str, object]:
        """Preview subtotal, discount, GST, and final amount without committing."""
        if cart.is_empty:
            raise EmptyCartError("Shopping cart is empty.")

        subtotal = cart.subtotal
        discount = self.discount_engine.calculate_discount(subtotal, promo_code=promo_code)
        taxable_amount = round(max(0.0, subtotal - discount.discount_amount), 2)
        gst_amount = round(taxable_amount * self.gst_rate, 2)
        final_amount = round(taxable_amount + gst_amount, 2)
        return {
            "subtotal": subtotal,
            "discount": discount,
            "taxable_amount": taxable_amount,
            "gst_rate": self.gst_rate,
            "gst_amount": gst_amount,
            "final_amount": final_amount,
        }

    def finalize_checkout(
        self,
        cart: ShoppingCart,
        *,
        customer_name: str = "Walk-in Customer",
        promo_code: Optional[str] = None,
        save_pdf: bool = True,
    ) -> Invoice:
        """
        Atomically validate stock, deduct inventory, persist transaction & items,
        and optionally generate a PDF invoice.
        """
        if cart.is_empty:
            raise EmptyCartError("Cannot finalize checkout: shopping cart is empty.")

        clean_customer = customer_name.strip() or "Walk-in Customer"
        summary = self.preview_totals(cart, promo_code=promo_code)
        discount = summary["discount"]  # type: ignore[assignment]
        subtotal = float(summary["subtotal"])
        taxable_amount = float(summary["taxable_amount"])
        gst_amount = float(summary["gst_amount"])
        final_amount = float(summary["final_amount"])

        bill_ref = self.generate_bill_reference()
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            InvoiceLine(
                product_id=item.product.product_id,
                product_name=item.product.name,
                category=item.product.category,
                unit_price=item.product.price,
                quantity=item.quantity,
                line_total=item.line_total,
            )
            for item in cart.items
        ]

        stock_batch = [(line.product_id, line.quantity) for line in lines]

        with self.db.connection() as conn:
            self.inventory.deduct_stock_in_conn(conn, stock_batch)

            cursor = conn.execute(
                """
                INSERT INTO transactions (
                    bill_ref, customer_name, subtotal, discount_type, promo_code,
                    discount_percent, discount_amount, taxable_amount, gst_rate,
                    gst_amount, final_amount, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bill_ref,
                    clean_customer,
                    subtotal,
                    discount.discount_type,
                    discount.promo_code,
                    discount.discount_percent,
                    discount.discount_amount,
                    taxable_amount,
                    self.gst_rate,
                    gst_amount,
                    final_amount,
                    created_at,
                ),
            )
            tx_id = int(cursor.lastrowid)

            conn.executemany(
                """
                INSERT INTO transaction_items (
                    transaction_id, product_id, product_name, category_name,
                    unit_price, quantity, line_total
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        tx_id,
                        line.product_id,
                        line.product_name,
                        line.category,
                        line.unit_price,
                        line.quantity,
                        line.line_total,
                    )
                    for line in lines
                ],
            )

        invoice = Invoice(
            bill_ref=bill_ref,
            customer_name=clean_customer,
            created_at=created_at,
            items=lines,
            subtotal=subtotal,
            discount_type=discount.discount_type,
            promo_code=discount.promo_code,
            discount_percent=discount.discount_percent,
            discount_amount=discount.discount_amount,
            taxable_amount=taxable_amount,
            gst_rate=self.gst_rate,
            gst_amount=gst_amount,
            final_amount=final_amount,
        )

        if save_pdf:
            pdf_file = self.export_invoice_pdf(invoice)
            invoice.pdf_path = str(pdf_file)
            with self.db.connection() as conn:
                conn.execute(
                    "UPDATE transactions SET pdf_path = ? WHERE bill_ref = ?",
                    (invoice.pdf_path, invoice.bill_ref),
                )

        cart.clear()
        return invoice

    def get_invoice_by_ref(self, bill_ref: str) -> Optional[Invoice]:
        """Retrieve a previously saved invoice and its line items from SQLite."""
        clean_ref = bill_ref.strip().upper()
        with self.db.connection() as conn:
            tx = conn.execute(
                "SELECT * FROM transactions WHERE UPPER(bill_ref) = ?",
                (clean_ref,),
            ).fetchone()
            if tx is None:
                return None

            item_rows = conn.execute(
                """
                SELECT product_id, product_name, category_name, unit_price, quantity, line_total
                FROM transaction_items
                WHERE transaction_id = ?
                ORDER BY item_id ASC
                """,
                (tx["transaction_id"],),
            ).fetchall()

        lines = [
            InvoiceLine(
                product_id=r["product_id"],
                product_name=r["product_name"],
                category=r["category_name"],
                unit_price=float(r["unit_price"]),
                quantity=int(r["quantity"]),
                line_total=float(r["line_total"]),
            )
            for r in item_rows
        ]
        return Invoice(
            bill_ref=tx["bill_ref"],
            customer_name=tx["customer_name"],
            created_at=tx["created_at"],
            items=lines,
            subtotal=float(tx["subtotal"]),
            discount_type=tx["discount_type"],
            promo_code=tx["promo_code"],
            discount_percent=float(tx["discount_percent"]),
            discount_amount=float(tx["discount_amount"]),
            taxable_amount=float(tx["taxable_amount"]),
            gst_rate=float(tx["gst_rate"]),
            gst_amount=float(tx["gst_amount"]),
            final_amount=float(tx["final_amount"]),
            pdf_path=tx["pdf_path"],
        )

    def format_invoice_text(self, invoice: Invoice) -> str:
        """Render a structured text invoice for console display."""
        border = "=" * 72
        divider = "-" * 72
        out = [
            border,
            "RETAIL SHOP MANAGEMENT SYSTEM - TAX INVOICE".center(72),
            border,
            f"Bill Ref   : {invoice.bill_ref}",
            f"Date/Time  : {invoice.created_at}",
            f"Customer   : {invoice.customer_name}",
            divider,
            f"{'ID':<10} {'Item':<30} {'Qty':>5} {'Price':>11} {'Total':>12}",
            divider,
        ]
        for line in invoice.items:
            short_name = (line.product_name[:28] + "..") if len(line.product_name) > 30 else line.product_name
            out.append(
                f"{line.product_id:<10} {short_name:<30} {line.quantity:>5} "
                f"{line.unit_price:>11.2f} {line.line_total:>12.2f}"
            )
        out.append(divider)
        out.append(f"{'Subtotal (INR):':<58} {invoice.subtotal:>13.2f}")
        if invoice.discount_amount > 0:
            label = (
                f"Discount ({invoice.promo_code} {invoice.discount_percent:.0f}%):"
                if invoice.promo_code
                else f"Auto Discount ({invoice.discount_percent:.0f}%):"
            )
            out.append(f"{label:<58} {-invoice.discount_amount:>13.2f}")
        else:
            out.append(f"{'Discount:':<58} {0.0:>13.2f}")
        out.append(f"{'Taxable Amount:':<58} {invoice.taxable_amount:>13.2f}")
        out.append(f"{f'GST ({invoice.gst_rate * 100:.0f}%):':<58} {invoice.gst_amount:>13.2f}")
        out.append(divider)
        out.append(f"{'FINAL PAYABLE AMOUNT (INR):':<58} {invoice.final_amount:>13.2f}")
        out.append(border)
        if invoice.pdf_path:
            out.append(f"PDF Saved  : {invoice.pdf_path}")
        return "\n".join(out)

    def export_invoice_pdf(self, invoice: Invoice) -> Path:
        """Export an invoice to a formatted PDF document inside invoices/."""
        self.invoices_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.invoices_dir / f"{invoice.bill_ref}.pdf"

        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet

            doc = SimpleDocTemplate(
                str(output_path),
                pagesize=A4,
                leftMargin=16 * mm,
                rightMargin=16 * mm,
                topMargin=16 * mm,
                bottomMargin=16 * mm,
            )
            styles = getSampleStyleSheet()
            elements = [
                Paragraph("<b>RETAIL SHOP MANAGEMENT SYSTEM</b>", styles["Title"]),
                Paragraph("Tax Invoice", styles["Heading2"]),
                Spacer(1, 6),
                Paragraph(
                    f"<b>Bill Reference:</b> {invoice.bill_ref}<br/>"
                    f"<b>Date:</b> {invoice.created_at}<br/>"
                    f"<b>Customer:</b> {invoice.customer_name}",
                    styles["Normal"],
                ),
                Spacer(1, 12),
            ]

            table_data = [["Product ID", "Product Name", "Qty", "Unit Price (INR)", "Total (INR)"]]
            for line in invoice.items:
                table_data.append(
                    [
                        line.product_id,
                        line.product_name,
                        str(line.quantity),
                        f"{line.unit_price:,.2f}",
                        f"{line.line_total:,.2f}",
                    ]
                )

            item_table = Table(table_data, colWidths=[24 * mm, 76 * mm, 16 * mm, 30 * mm, 30 * mm])
            item_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            elements.append(item_table)
            elements.append(Spacer(1, 14))

            discount_label = (
                f"Discount ({invoice.promo_code} - {invoice.discount_percent:.0f}%)"
                if invoice.promo_code
                else (
                    f"Automatic Discount ({invoice.discount_percent:.0f}%)"
                    if invoice.discount_amount > 0
                    else "Discount"
                )
            )
            summary_data = [
                ["Subtotal", f"INR {invoice.subtotal:,.2f}"],
                [discount_label, f"- INR {invoice.discount_amount:,.2f}"],
                ["Taxable Amount", f"INR {invoice.taxable_amount:,.2f}"],
                [f"GST ({invoice.gst_rate * 100:.0f}%)", f"INR {invoice.gst_amount:,.2f}"],
                ["Final Payable Amount", f"INR {invoice.final_amount:,.2f}"],
            ]
            summary_table = Table(summary_data, colWidths=[120 * mm, 56 * mm])
            summary_table.setStyle(
                TableStyle(
                    [
                        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                        ("LINEABOVE", (0, -1), (-1, -1), 1.0, colors.HexColor("#0F172A")),
                        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            elements.append(summary_table)
            doc.build(elements)
        except ImportError:
            text_lines = self.format_invoice_text(invoice).splitlines()
            _write_minimal_pdf(text_lines, output_path)

        return output_path
