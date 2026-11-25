"""Secure administrative panel with PBKDF2 authentication, product control, and sales monitoring."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from database import DatabaseManager, hash_password, verify_password
from inventory import InventoryManager
from models import (
    AdminAccount,
    AuthenticationError,
    LOW_STOCK_DEFAULT_THRESHOLD,
    Product,
    ValidationError,
)


class AdminManager:
    """Handles admin authentication, inventory administration, and daily sales monitoring."""

    def __init__(self, db: DatabaseManager, inventory: InventoryManager) -> None:
        self.db = db
        self.inventory = inventory
        self._current_admin: Optional[AdminAccount] = None

    @property
    def is_authenticated(self) -> bool:
        return self._current_admin is not None

    @property
    def current_admin(self) -> Optional[AdminAccount]:
        return self._current_admin

    def _require_auth(self) -> AdminAccount:
        if self._current_admin is None:
            raise AuthenticationError("Administrator authentication is required for this action.")
        return self._current_admin

    def login(self, username: str, password: str) -> AdminAccount:
        """Validate username and password against hashed credentials in SQLite."""
        clean_user = username.strip()
        if not clean_user or not password:
            raise AuthenticationError("Username and password must not be empty.")

        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT admin_id, username, password_hash, role, last_login
                FROM admins
                WHERE username = ?
                """,
                (clean_user,),
            ).fetchone()

            if row is None or not verify_password(password, row["password_hash"]):
                raise AuthenticationError("Invalid administrator username or password.")

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE admins SET last_login = ? WHERE admin_id = ?",
                (now_str, row["admin_id"]),
            )

        self._current_admin = AdminAccount(
            admin_id=int(row["admin_id"]),
            username=row["username"],
            role=row["role"],
            last_login=now_str,
        )
        return self._current_admin

    def logout(self) -> None:
        """End the active administrator session."""
        self._current_admin = None

    def create_admin(self, username: str, password: str, role: str = "admin") -> AdminAccount:
        """Create a new administrator account with a salted PBKDF2 password hash."""
        self._require_auth()
        clean_user = username.strip()
        if len(clean_user) < 3:
            raise ValidationError("Admin username must be at least 3 characters.")
        if len(password) < 6:
            raise ValidationError("Admin password must be at least 6 characters.")

        pw_hash = hash_password(password)
        with self.db.connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM admins WHERE username = ?",
                (clean_user,),
            ).fetchone()
            if existing:
                raise ValidationError(f"Admin username '{clean_user}' already exists.")

            cur = conn.execute(
                "INSERT INTO admins (username, password_hash, role) VALUES (?, ?, ?)",
                (clean_user, pw_hash, role.strip() or "admin"),
            )
            admin_id = int(cur.lastrowid)

        return AdminAccount(admin_id=admin_id, username=clean_user, role=role)

    def add_product(self, product: Product) -> Product:
        self._require_auth()
        return self.inventory.add_product(product)

    def update_product(self, product_id: str, **fields: object) -> Product:
        self._require_auth()
        return self.inventory.update_product(product_id, **fields)  # type: ignore[arg-type]

    def delete_product(self, product_id: str) -> None:
        self._require_auth()
        self.inventory.delete_product(product_id)

    def restock_product(self, product_id: str, quantity_to_add: int) -> Product:
        self._require_auth()
        return self.inventory.restock_product(product_id, quantity_to_add)

    def get_low_stock_alerts(
        self,
        threshold: int = LOW_STOCK_DEFAULT_THRESHOLD,
    ) -> list[Product]:
        self._require_auth()
        return self.inventory.get_low_stock_products(threshold=threshold)

    def get_recent_transactions(self, limit: int = 25) -> list[dict[str, object]]:
        """Monitor recent customer transactions."""
        self._require_auth()
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT bill_ref, customer_name, subtotal, discount_type, promo_code,
                       discount_amount, gst_amount, final_amount, created_at
                FROM transactions
                ORDER BY transaction_id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_daily_sales_report(self, target_date: Optional[str] = None) -> dict[str, object]:
        """Generate a daily sales summary for a given YYYY-MM-DD date (defaults to today)."""
        self._require_auth()
        date_str = target_date or datetime.now().strftime("%Y-%m-%d")

        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS transaction_count,
                    COALESCE(SUM(subtotal), 0.0) AS gross_sales,
                    COALESCE(SUM(discount_amount), 0.0) AS total_discounts,
                    COALESCE(SUM(gst_amount), 0.0) AS total_gst,
                    COALESCE(SUM(final_amount), 0.0) AS net_revenue
                FROM transactions
                WHERE substr(created_at, 1, 10) = ?
                """,
                (date_str,),
            ).fetchone()

            tx_rows = conn.execute(
                """
                SELECT bill_ref, customer_name, final_amount, discount_amount, created_at
                FROM transactions
                WHERE substr(created_at, 1, 10) = ?
                ORDER BY transaction_id DESC
                """,
                (date_str,),
            ).fetchall()

        return {
            "date": date_str,
            "transaction_count": int(row["transaction_count"]) if row else 0,
            "gross_sales": round(float(row["gross_sales"]), 2) if row else 0.0,
            "total_discounts": round(float(row["total_discounts"]), 2) if row else 0.0,
            "total_gst": round(float(row["total_gst"]), 2) if row else 0.0,
            "net_revenue": round(float(row["net_revenue"]), 2) if row else 0.0,
            "transactions": [dict(r) for r in tx_rows],
        }
