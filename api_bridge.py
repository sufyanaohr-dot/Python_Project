"""JSON CLI bridge allowing the web dashboard to execute real Python services on retail_store.db."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from admin import AdminManager
from analytics import SalesAnalytics
from billing import BillingManager
from checkout import DiscountEngine, ShoppingCart
from database import DEFAULT_DB_PATH, DatabaseManager
from inventory import InventoryManager
from models import Product, RetailError


def _make_services() -> tuple[DatabaseManager, InventoryManager, BillingManager, AdminManager, SalesAnalytics, DiscountEngine]:
    db = DatabaseManager(DEFAULT_DB_PATH)
    inv = InventoryManager(db)
    bill = BillingManager(db, inv)
    adm = AdminManager(db, inv)
    ana = SalesAnalytics(db, inv)
    disc = DiscountEngine(db)
    return db, inv, bill, adm, ana, disc


def handle_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action", "")).strip()
    _, inv, bill, adm, ana, disc = _make_services()

    if action == "overview":
        counts = inv.get_category_counts()
        summary = ana.get_sales_summary()
        low_stock = inv.get_low_stock_products()
        return {
            "totalProducts": inv.count_products(),
            "categoryCounts": counts,
            "salesSummary": asdict(summary),
            "lowStockCount": len(low_stock),
            "promoCodes": disc.list_active_promo_codes(),
        }

    if action == "products":
        query = str(payload.get("query", "")).strip()
        category = str(payload.get("category", "")).strip()
        page = max(1, int(payload.get("page", 1)))
        limit = min(100, max(1, int(payload.get("limit", 24))))
        offset = (page - 1) * limit

        if query:
            items = inv.search_products(query, limit=200)
            if category and category != "All":
                items = [p for p in items if p.category == category]
            total = len(items)
            paged = items[offset : offset + limit]
        elif category and category != "All":
            total = inv.count_products(category=category)
            paged = inv.filter_by_category(category, limit=limit, offset=offset)
        else:
            total = inv.count_products()
            paged = inv.list_products(limit=limit, offset=offset)

        return {
            "products": [asdict(p) for p in paged],
            "total": total,
            "page": page,
            "limit": limit,
        }

    if action == "preview_checkout":
        cart = ShoppingCart(inv)
        for row in payload.get("items", []):
            cart.add_item(str(row["product_id"]), int(row["quantity"]))
        promo = payload.get("promo_code")
        summary = bill.preview_totals(cart, promo_code=promo)
        return {
            "subtotal": summary["subtotal"],
            "discount": asdict(summary["discount"]),
            "taxable_amount": summary["taxable_amount"],
            "gst_rate": summary["gst_rate"],
            "gst_amount": summary["gst_amount"],
            "final_amount": summary["final_amount"],
        }

    if action == "checkout":
        cart = ShoppingCart(inv)
        for row in payload.get("items", []):
            cart.add_item(str(row["product_id"]), int(row["quantity"]))
        invoice = bill.finalize_checkout(
            cart,
            customer_name=str(payload.get("customer_name") or "Walk-in Customer"),
            promo_code=payload.get("promo_code"),
            save_pdf=True,
        )
        return {
            "invoice": asdict(invoice),
            "invoiceText": bill.format_invoice_text(invoice),
        }

    if action == "invoice":
        ref = str(payload.get("bill_ref", "")).strip()
        inv_obj = bill.get_invoice_by_ref(ref)
        if inv_obj is None:
            return {"invoice": None}
        return {
            "invoice": asdict(inv_obj),
            "invoiceText": bill.format_invoice_text(inv_obj),
        }

    if action == "analytics":
        summary = ana.get_sales_summary()
        return {
            "summary": asdict(summary),
            "bestSellers": ana.get_best_selling_products(top_n=10),
            "categorySales": ana.get_category_wise_sales(),
            "lowStock": [asdict(p) for p in ana.get_low_stock_products()[:30]],
        }

    if action == "admin_login":
        account = adm.login(str(payload.get("username", "")), str(payload.get("password", "")))
        return {"admin": asdict(account)}

    if action == "admin_action":
        adm.login(str(payload.get("username", "")), str(payload.get("password", "")))
        op = str(payload.get("op", ""))
        if op == "add":
            p = payload["product"]
            created = adm.add_product(
                Product(
                    product_id=str(p["product_id"]),
                    name=str(p["name"]),
                    category=str(p["category"]),
                    price=float(p["price"]),
                    stock=int(p["stock"]),
                    sku=str(p["sku"]),
                )
            )
            return {"product": asdict(created)}
        if op == "update":
            p = payload["product"]
            updated = adm.update_product(
                str(p["product_id"]),
                name=p.get("name"),
                category=p.get("category"),
                price=float(p["price"]) if p.get("price") is not None else None,
                stock=int(p["stock"]) if p.get("stock") is not None else None,
                sku=p.get("sku"),
            )
            return {"product": asdict(updated)}
        if op == "restock":
            updated = adm.restock_product(str(payload["product_id"]), int(payload["quantity"]))
            return {"product": asdict(updated)}
        if op == "delete":
            adm.delete_product(str(payload["product_id"]))
            return {"deleted": str(payload["product_id"])}
        if op == "daily_report":
            return {"report": adm.get_daily_sales_report(payload.get("date"))}
        if op == "recent_transactions":
            return {"transactions": adm.get_recent_transactions(limit=int(payload.get("limit", 25)))}

    raise ValueError(f"Unsupported action: {action}")


def main() -> None:
    try:
        raw = sys.stdin.read().strip() or "{}"
        payload = json.loads(raw)
        result = handle_action(payload)
        print(json.dumps({"ok": True, "data": result}))
    except (RetailError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
