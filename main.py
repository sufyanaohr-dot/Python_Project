"""Interactive CLI entry point for the Retail Shop Management System."""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Optional

from admin import AdminManager
from analytics import SalesAnalytics
from billing import BillingManager
from checkout import DiscountEngine, ShoppingCart
from database import DEFAULT_DB_PATH, DatabaseManager
from inventory import InventoryManager
from models import CATEGORIES, Product, RetailError


class RetailShopCLI:
    """Menu-driven console application orchestrating inventory, POS, admin, and analytics."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db = DatabaseManager(db_path=db_path)
        self.inventory = InventoryManager(self.db)
        self.billing = BillingManager(self.db, self.inventory)
        self.admin = AdminManager(self.db, self.inventory)
        self.analytics = SalesAnalytics(self.db, self.inventory)
        self.discount_engine = DiscountEngine(self.db)
        self.cart = ShoppingCart(self.inventory)

    @staticmethod
    def _prompt_int(message: str) -> int:
        raw = input(message).strip()
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"Expected a whole number, got '{raw}'.") from exc

    @staticmethod
    def _prompt_float(message: str) -> float:
        raw = input(message).strip()
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError(f"Expected a numeric price, got '{raw}'.") from exc

    @staticmethod
    def _print_products(products: list[Product]) -> None:
        if not products:
            print("No matching products found.")
            return
        print("-" * 92)
        print(f"{'ID':<10} {'Name':<38} {'Category':<16} {'Price (Rs.)':>12} {'Stock':>7}")
        print("-" * 92)
        for p in products:
            short_name = (p.name[:35] + "...") if len(p.name) > 38 else p.name
            print(
                f"{p.product_id:<10} {short_name:<38} {p.category:<16} "
                f"{p.price:>12.2f} {p.stock:>7}"
            )
        print("-" * 92)

    def run(self) -> None:
        """Start the main interactive terminal loop."""
        total = self.inventory.count_products()
        print("\n" + "=" * 68)
        print("RETAIL SHOP MANAGEMENT SYSTEM".center(68))
        print(f"SQLite Database Ready | Catalog Size: {total} Products".center(68))
        print("=" * 68)

        while True:
            print(
                "\nMAIN MENU\n"
                "  1. Customer Shopping & Checkout\n"
                "  2. Lookup Saved Invoice\n"
                "  3. Admin Panel (Protected)\n"
                "  4. Sales Analytics & Reports\n"
                "  0. Exit"
            )
            choice = input("Select an option: ").strip()

            try:
                if choice == "1":
                    self._customer_menu()
                elif choice == "2":
                    self._lookup_invoice()
                elif choice == "3":
                    self._admin_login_flow()
                elif choice == "4":
                    self._analytics_menu()
                elif choice == "0":
                    print("Thank you for using Retail Shop Management System. Goodbye!")
                    break
                else:
                    print("Invalid option. Please choose 0-4.")
            except (RetailError, ValueError) as exc:
                print(f"\n[Error] {exc}")

    def _customer_menu(self) -> None:
        while True:
            print(
                f"\nCUSTOMER SHOPPING (Cart Items: {self.cart.total_units} | "
                f"Subtotal: Rs. {self.cart.subtotal:,.2f})\n"
                "  1. Browse All Products (Paginated)\n"
                "  2. Search Products by ID or Name\n"
                "  3. Filter Products by Category\n"
                "  4. Add Product to Cart\n"
                "  5. Update Cart Item Quantity\n"
                "  6. Remove Product from Cart\n"
                "  7. View Cart & Subtotal\n"
                "  8. Checkout & Generate PDF Invoice\n"
                "  0. Back to Main Menu"
            )
            choice = input("Select an option: ").strip()

            try:
                if choice == "1":
                    page = self._prompt_int("Enter page number (1-based): ")
                    page_size = 20
                    offset = max(0, (page - 1) * page_size)
                    products = self.inventory.list_products(limit=page_size, offset=offset)
                    self._print_products(products)
                elif choice == "2":
                    query = input("Enter Product ID, Name, or SKU: ")
                    self._print_products(self.inventory.search_products(query, limit=30))
                elif choice == "3":
                    print("Categories:", ", ".join(CATEGORIES))
                    cat = input("Enter category name: ")
                    products = self.inventory.filter_by_category(cat, limit=30)
                    self._print_products(products)
                elif choice == "4":
                    pid = input("Enter Product ID: ")
                    qty = self._prompt_int("Enter quantity: ")
                    item = self.cart.add_item(pid, qty)
                    print(f"Added {item.quantity}x {item.product.name} to cart.")
                elif choice == "5":
                    pid = input("Enter Product ID in cart: ")
                    qty = self._prompt_int("Enter new quantity (0 to remove): ")
                    updated = self.cart.update_quantity(pid, qty)
                    if updated:
                        print(f"Updated {updated.product.name} quantity to {updated.quantity}.")
                    else:
                        print("Item removed from cart.")
                elif choice == "6":
                    pid = input("Enter Product ID to remove: ")
                    self.cart.remove_item(pid)
                    print("Removed item from cart.")
                elif choice == "7":
                    self._display_cart()
                elif choice == "8":
                    self._checkout_flow()
                elif choice == "0":
                    break
                else:
                    print("Invalid choice.")
            except (RetailError, ValueError, KeyError) as exc:
                print(f"\n[Error] {exc}")

    def _display_cart(self) -> None:
        if self.cart.is_empty:
            print("Your shopping cart is empty.")
            return
        print("-" * 78)
        print(f"{'ID':<10} {'Product Name':<34} {'Qty':>5} {'Unit (Rs.)':>12} {'Total (Rs.)':>13}")
        print("-" * 78)
        for item in self.cart.items:
            print(
                f"{item.product.product_id:<10} {item.product.name[:34]:<34} "
                f"{item.quantity:>5} {item.product.price:>12.2f} {item.line_total:>13.2f}"
            )
        print("-" * 78)
        print(f"Cart Subtotal: Rs. {self.cart.subtotal:,.2f}")

    def _checkout_flow(self) -> None:
        self._display_cart()
        if self.cart.is_empty:
            return
        customer = input("Customer Name (press Enter for Walk-in Customer): ").strip()
        promos = self.discount_engine.list_active_promo_codes()
        promo_hint = ", ".join(f"{p['code']} ({p['discount_percent']:.0f}%)" for p in promos)
        print(f"Available Promo Codes: {promo_hint} | Auto 5% off above Rs. 1000 (non-stackable)")
        code_input: Optional[str] = input("Enter Promo Code (optional, press Enter to skip): ").strip() or None

        invoice = self.billing.finalize_checkout(
            self.cart,
            customer_name=customer or "Walk-in Customer",
            promo_code=code_input,
            save_pdf=True,
        )
        print("\n" + self.billing.format_invoice_text(invoice))

    def _lookup_invoice(self) -> None:
        ref = input("Enter Bill Reference ID (e.g. INV-...): ").strip()
        invoice = self.billing.get_invoice_by_ref(ref)
        if invoice is None:
            print(f"No invoice found for reference '{ref}'.")
        else:
            print("\n" + self.billing.format_invoice_text(invoice))

    def _admin_login_flow(self) -> None:
        if not self.admin.is_authenticated:
            username = input("Admin Username: ").strip()
            password = getpass.getpass("Admin Password: ")
            account = self.admin.login(username, password)
            print(f"Welcome, {account.username} ({account.role}).")

        while self.admin.is_authenticated:
            print(
                "\nADMIN PANEL\n"
                "  1. Add New Product\n"
                "  2. Update Product Details\n"
                "  3. Restock Existing Product\n"
                "  4. Delete Product\n"
                "  5. View Low-Stock Alerts\n"
                "  6. Monitor Recent Transactions\n"
                "  7. Daily Sales Report\n"
                "  8. Logout\n"
                "  0. Return to Main Menu"
            )
            choice = input("Select an option: ").strip()
            try:
                if choice == "1":
                    pid = input("New Product ID (e.g. PRD-1001): ")
                    name = input("Product Name: ")
                    print("Categories:", ", ".join(CATEGORIES))
                    category = input("Category: ")
                    price = self._prompt_float("Price (Rs.): ")
                    stock = self._prompt_int("Initial Stock: ")
                    sku = input("Unique SKU: ")
                    created = self.admin.add_product(
                        Product(
                            product_id=pid,
                            name=name,
                            category=category,
                            price=price,
                            stock=stock,
                            sku=sku,
                        )
                    )
                    print(f"Created product {created.product_id}: {created.name}")
                elif choice == "2":
                    pid = input("Product ID to update: ")
                    new_name = input("New Name (leave blank to keep): ").strip() or None
                    new_price_raw = input("New Price (leave blank to keep): ").strip()
                    new_price = float(new_price_raw) if new_price_raw else None
                    updated = self.admin.update_product(pid, name=new_name, price=new_price)
                    print(f"Updated {updated.product_id}: {updated.name} @ Rs. {updated.price:.2f}")
                elif choice == "3":
                    pid = input("Product ID to restock: ")
                    qty = self._prompt_int("Quantity to add: ")
                    updated = self.admin.restock_product(pid, qty)
                    print(f"Restocked {updated.product_id}. New stock: {updated.stock}")
                elif choice == "4":
                    pid = input("Product ID to delete: ")
                    self.admin.delete_product(pid)
                    print(f"Deleted product '{pid}'.")
                elif choice == "5":
                    alerts = self.admin.get_low_stock_alerts()
                    self._print_products(alerts[:40])
                elif choice == "6":
                    txs = self.admin.get_recent_transactions(limit=20)
                    for tx in txs:
                        print(
                            f"{tx['bill_ref']} | {tx['created_at']} | "
                            f"{tx['customer_name']} | Rs. {tx['final_amount']:.2f}"
                        )
                elif choice == "7":
                    date_str = input("Target Date YYYY-MM-DD (blank for today): ").strip() or None
                    report = self.admin.get_daily_sales_report(date_str)
                    print(
                        f"\nDaily Sales ({report['date']}): "
                        f"{report['transaction_count']} transactions | "
                        f"Net Revenue: Rs. {report['net_revenue']:,.2f} | "
                        f"Discounts: Rs. {report['total_discounts']:,.2f}"
                    )
                elif choice == "8":
                    self.admin.logout()
                    print("Logged out of Admin Panel.")
                    break
                elif choice == "0":
                    break
                else:
                    print("Invalid option.")
            except (RetailError, ValueError) as exc:
                print(f"\n[Error] {exc}")

    def _analytics_menu(self) -> None:
        print("\n" + self.analytics.format_analytics_report())
        export = input("Generate Matplotlib PNG charts in reports/? (y/N): ").strip().lower()
        if export == "y":
            files = self.analytics.generate_sales_charts()
            if files:
                print("Generated chart files:")
                for f in files:
                    print(f"  - {f}")
            else:
                print("Charts unavailable (install matplotlib via requirements.txt or no sales yet).")


if __name__ == "__main__":
    RetailShopCLI().run()
