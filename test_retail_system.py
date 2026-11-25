"""Unit and integration test suite for the Retail Shop Management System."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from admin import AdminManager
from analytics import SalesAnalytics
from billing import BillingManager
from checkout import DiscountEngine, ShoppingCart
from database import DatabaseManager
from inventory import InventoryManager
from models import (
    CATEGORIES,
    AuthenticationError,
    DuplicateProductError,
    EmptyCartError,
    InsufficientStockError,
    InvalidPriceError,
    InvalidPromoCodeError,
    InvalidQuantityError,
    Product,
    ProductNotFoundError,
)


class TestRetailShopManagementSystem(unittest.TestCase):
    """Verifies database seeding, inventory CRUD, cart, discounts, GST billing, admin, and analytics."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_retail.db"
        self.invoices_dir = Path(self.temp_dir.name) / "invoices"
        self.db = DatabaseManager(db_path=self.db_path)
        self.inventory = InventoryManager(self.db)
        self.billing = BillingManager(self.db, self.inventory, invoices_dir=self.invoices_dir)
        self.admin = AdminManager(self.db, self.inventory)
        self.analytics = SalesAnalytics(self.db, self.inventory)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initial_1000_products_and_categories(self) -> None:
        self.assertEqual(self.inventory.count_products(), 1000)
        counts = self.inventory.get_category_counts()
        self.assertEqual(len(counts), 5)
        for category in CATEGORIES:
            self.assertEqual(counts[category], 200)

    def test_inventory_search_filter_and_crud(self) -> None:
        p = self.inventory.get_product("PRD-0001")
        self.assertEqual(p.category, "Electronics")

        matches = self.inventory.search_products("PRD-0001")
        self.assertEqual(matches[0].product_id, "PRD-0001")

        clothing = self.inventory.filter_by_category("Clothing", limit=500)
        self.assertEqual(len(clothing), 200)

        new_prod = Product(
            product_id="PRD-9999",
            name="Test Mechanical Keyboard",
            category="Electronics",
            price=2499.0,
            stock=15,
            sku="SKU-TEST-9999",
        )
        created = self.inventory.add_product(new_prod)
        self.assertEqual(created.price, 2499.0)

        with self.assertRaises(DuplicateProductError):
            self.inventory.add_product(new_prod)

        updated = self.inventory.update_product("PRD-9999", price=2799.0)
        self.assertEqual(updated.price, 2799.0)

        restocked = self.inventory.restock_product("PRD-9999", 10)
        self.assertEqual(restocked.stock, 25)

        self.inventory.delete_product("PRD-9999")
        with self.assertRaises(ProductNotFoundError):
            self.inventory.get_product("PRD-9999")

    def test_validation_errors(self) -> None:
        with self.assertRaises(InvalidPriceError):
            Product("PRD-BAD1", "Bad Price", "Grocery", -10.0, 5, "SKU-BAD-1")

        with self.assertRaises(InvalidQuantityError):
            Product("PRD-BAD2", "Bad Stock", "Grocery", 50.0, -3, "SKU-BAD-2")

        cart = ShoppingCart(self.inventory)
        with self.assertRaises(InvalidQuantityError):
            cart.add_item("PRD-0001", 0)

        prod = self.inventory.get_product("PRD-0001")
        with self.assertRaises(InsufficientStockError):
            cart.add_item("PRD-0001", prod.stock + 10)

        with self.assertRaises(EmptyCartError):
            self.billing.finalize_checkout(cart)

    def test_non_stackable_discounts(self) -> None:
        engine = DiscountEngine(self.db)

        auto = engine.calculate_discount(1500.0, promo_code=None)
        self.assertEqual(auto.discount_type, "AUTO")
        self.assertEqual(auto.discount_percent, 5.0)
        self.assertEqual(auto.discount_amount, 75.0)

        promo_20 = engine.calculate_discount(1500.0, promo_code="SAVE20")
        self.assertEqual(promo_20.discount_type, "PROMO")
        self.assertEqual(promo_20.discount_percent, 20.0)
        self.assertEqual(promo_20.discount_amount, 300.0)

        no_auto = engine.calculate_discount(800.0, promo_code=None)
        self.assertEqual(no_auto.discount_type, "NONE")
        self.assertEqual(no_auto.discount_amount, 0.0)

        with self.assertRaises(InvalidPromoCodeError):
            engine.calculate_discount(1200.0, promo_code="EXPIRED99")

    def test_checkout_gst_pdf_and_persistence(self) -> None:
        prod = Product("PRD-8888", "Studio Desk Lamp", "Home Appliances", 2000.0, 20, "SKU-TEST-8888")
        self.inventory.add_product(prod)

        cart = ShoppingCart(self.inventory)
        cart.add_item("PRD-8888", 2)  # Subtotal = 4000.00
        self.assertEqual(cart.subtotal, 4000.0)

        invoice = self.billing.finalize_checkout(
            cart,
            customer_name="Aarav Sharma",
            promo_code="SAVE10",
            save_pdf=True,
        )
        self.assertTrue(invoice.bill_ref.startswith("INV-"))
        self.assertEqual(invoice.subtotal, 4000.0)
        self.assertEqual(invoice.discount_amount, 400.0)  # 10% SAVE10
        self.assertEqual(invoice.taxable_amount, 3600.0)
        self.assertEqual(invoice.gst_amount, 648.0)  # 18% GST
        self.assertEqual(invoice.final_amount, 4248.0)
        self.assertIsNotNone(invoice.pdf_path)
        self.assertTrue(Path(invoice.pdf_path).exists())
        self.assertTrue(cart.is_empty)

        # Verify stock deducted and persisted across fresh DatabaseManager instance
        fresh_db = DatabaseManager(db_path=self.db_path)
        fresh_inv = InventoryManager(fresh_db)
        fresh_billing = BillingManager(fresh_db, fresh_inv, invoices_dir=self.invoices_dir)

        self.assertEqual(fresh_inv.get_product("PRD-8888").stock, 18)
        loaded_invoice = fresh_billing.get_invoice_by_ref(invoice.bill_ref)
        self.assertIsNotNone(loaded_invoice)
        self.assertEqual(loaded_invoice.final_amount, 4248.0)

    def test_admin_authentication_and_analytics(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.admin.get_daily_sales_report()

        with self.assertRaises(AuthenticationError):
            self.admin.login("admin", "wrong-password")

        account = self.admin.login("admin", "Admin@123")
        self.assertEqual(account.username, "admin")

        cart = ShoppingCart(self.inventory)
        cart.add_item("PRD-0002", 1)
        self.billing.finalize_checkout(cart, promo_code="WELCOME5", save_pdf=False)

        summary = self.analytics.get_sales_summary()
        self.assertEqual(summary.total_transactions, 1)
        self.assertGreater(summary.total_revenue, 0.0)
        self.assertGreater(len(self.analytics.get_best_selling_products()), 0)
        self.assertEqual(len(self.analytics.get_category_wise_sales()), 5)
        self.assertGreater(len(self.analytics.get_low_stock_products()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
