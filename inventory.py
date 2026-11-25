"""Product inventory management service backed by SQLite."""

from __future__ import annotations

import sqlite3
from typing import Optional

from database import DatabaseManager
from models import (
    CATEGORIES,
    LOW_STOCK_DEFAULT_THRESHOLD,
    DuplicateProductError,
    InsufficientStockError,
    InvalidPriceError,
    InvalidQuantityError,
    Product,
    ProductNotFoundError,
    ValidationError,
)


class InventoryManager:
    """Provides CRUD, search, filtering, and stock control for retail products."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> Product:
        return Product(
            product_id=row["product_id"],
            name=row["name"],
            category=row["category_name"],
            price=float(row["price"]),
            stock=int(row["stock"]),
            sku=row["sku"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _get_category_id(self, conn: sqlite3.Connection, category_name: str) -> int:
        clean = category_name.strip()
        if clean not in CATEGORIES:
            raise ValidationError(
                f"Invalid category '{category_name}'. Allowed: {', '.join(CATEGORIES)}."
            )
        row = conn.execute(
            "SELECT category_id FROM categories WHERE name = ?",
            (clean,),
        ).fetchone()
        if not row:
            raise ValidationError(f"Category '{clean}' is not registered in database.")
        return int(row["category_id"])

    def count_products(self, category: Optional[str] = None) -> int:
        """Return total product count, optionally scoped to a single category."""
        with self.db.connection() as conn:
            if category:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM products p
                    JOIN categories c ON c.category_id = p.category_id
                    WHERE LOWER(c.name) = LOWER(?)
                    """,
                    (category.strip(),),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS cnt FROM products").fetchone()
            return int(row["cnt"]) if row else 0

    def get_category_counts(self) -> dict[str, int]:
        """Return product count grouped by category."""
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT c.name AS category_name, COUNT(p.product_id) AS cnt
                FROM categories c
                LEFT JOIN products p ON p.category_id = c.category_id
                GROUP BY c.category_id, c.name
                ORDER BY c.category_id ASC
                """
            ).fetchall()
            return {row["category_name"]: int(row["cnt"]) for row in rows}

    def list_products(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[Product]:
        """View all products ordered by Product ID with optional pagination."""
        query = """
            SELECT p.product_id, p.name, c.name AS category_name, p.price, p.stock,
                   p.sku, p.created_at, p.updated_at
            FROM products p
            JOIN categories c ON c.category_id = p.category_id
            ORDER BY p.product_id ASC
        """
        params: list[object] = []
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([int(limit), max(0, int(offset))])

        with self.db.connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_product(r) for r in rows]

    def get_product(self, product_id: str) -> Product:
        """Fetch a single product by unique Product ID or raise ProductNotFoundError."""
        clean_id = product_id.strip().upper()
        if not clean_id:
            raise ProductNotFoundError("Product ID cannot be empty.")

        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT p.product_id, p.name, c.name AS category_name, p.price, p.stock,
                       p.sku, p.created_at, p.updated_at
                FROM products p
                JOIN categories c ON c.category_id = p.category_id
                WHERE UPPER(p.product_id) = ?
                """,
                (clean_id,),
            ).fetchone()
            if row is None:
                raise ProductNotFoundError(f"Product ID '{clean_id}' was not found.")
            return self._row_to_product(row)

    def search_products(self, query: str, limit: int = 100) -> list[Product]:
        """Search products by partial or exact Product ID, Name, or SKU."""
        clean = query.strip()
        if not clean:
            return self.list_products(limit=limit)

        pattern = f"%{clean}%"
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT p.product_id, p.name, c.name AS category_name, p.price, p.stock,
                       p.sku, p.created_at, p.updated_at
                FROM products p
                JOIN categories c ON c.category_id = p.category_id
                WHERE UPPER(p.product_id) LIKE UPPER(?)
                   OR LOWER(p.name) LIKE LOWER(?)
                   OR UPPER(p.sku) LIKE UPPER(?)
                ORDER BY
                    CASE WHEN UPPER(p.product_id) = UPPER(?) THEN 0 ELSE 1 END,
                    p.product_id ASC
                LIMIT ?
                """,
                (pattern, pattern, pattern, clean, int(limit)),
            ).fetchall()
            return [self._row_to_product(r) for r in rows]

    def filter_by_category(
        self,
        category: str,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[Product]:
        """Filter products by exact category name."""
        clean_cat = category.strip()
        if clean_cat not in CATEGORIES:
            raise ValidationError(
                f"Unknown category '{category}'. Choose from: {', '.join(CATEGORIES)}."
            )

        sql = """
            SELECT p.product_id, p.name, c.name AS category_name, p.price, p.stock,
                   p.sku, p.created_at, p.updated_at
            FROM products p
            JOIN categories c ON c.category_id = p.category_id
            WHERE c.name = ?
            ORDER BY p.product_id ASC
        """
        params: list[object] = [clean_cat]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), max(0, int(offset))])

        with self.db.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_product(r) for r in rows]

    def add_product(self, product: Product) -> Product:
        """Insert a new product after validating uniqueness of Product ID and SKU."""
        with self.db.connection() as conn:
            existing_id = conn.execute(
                "SELECT 1 FROM products WHERE UPPER(product_id) = ?",
                (product.product_id,),
            ).fetchone()
            if existing_id:
                raise DuplicateProductError(
                    f"Duplicate Product ID '{product.product_id}' already exists."
                )

            existing_sku = conn.execute(
                "SELECT 1 FROM products WHERE UPPER(sku) = ?",
                (product.sku,),
            ).fetchone()
            if existing_sku:
                raise DuplicateProductError(
                    f"Duplicate SKU '{product.sku}' is already assigned to another product."
                )

            cat_id = self._get_category_id(conn, product.category)
            conn.execute(
                """
                INSERT INTO products (product_id, name, category_id, price, stock, sku)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    product.product_id,
                    product.name,
                    cat_id,
                    product.price,
                    product.stock,
                    product.sku,
                ),
            )
        return self.get_product(product.product_id)

    def update_product(
        self,
        product_id: str,
        *,
        name: Optional[str] = None,
        category: Optional[str] = None,
        price: Optional[float] = None,
        stock: Optional[int] = None,
        sku: Optional[str] = None,
    ) -> Product:
        """Update one or more fields on an existing product."""
        current = self.get_product(product_id)

        new_name = current.name if name is None else name.strip()
        if not new_name:
            raise ValidationError("Product name cannot be empty.")

        new_category = current.category if category is None else category.strip()
        if new_category not in CATEGORIES:
            raise ValidationError(f"Invalid category '{new_category}'.")

        if price is None:
            new_price = current.price
        else:
            if not isinstance(price, (int, float)) or isinstance(price, bool) or float(price) <= 0:
                raise InvalidPriceError(f"Price must be positive, got {price!r}.")
            new_price = round(float(price), 2)

        if stock is None:
            new_stock = current.stock
        else:
            if not isinstance(stock, int) or isinstance(stock, bool) or stock < 0:
                raise InvalidQuantityError(f"Stock cannot be negative, got {stock!r}.")
            new_stock = stock

        new_sku = current.sku if sku is None else sku.strip().upper()
        if not new_sku:
            raise ValidationError("SKU cannot be empty.")

        with self.db.connection() as conn:
            if new_sku != current.sku:
                dup = conn.execute(
                    "SELECT 1 FROM products WHERE UPPER(sku) = ? AND UPPER(product_id) != ?",
                    (new_sku, current.product_id),
                ).fetchone()
                if dup:
                    raise DuplicateProductError(f"SKU '{new_sku}' is already in use.")

            cat_id = self._get_category_id(conn, new_category)
            conn.execute(
                """
                UPDATE products
                SET name = ?, category_id = ?, price = ?, stock = ?, sku = ?,
                    updated_at = datetime('now', 'localtime')
                WHERE UPPER(product_id) = ?
                """,
                (new_name, cat_id, new_price, new_stock, new_sku, current.product_id),
            )
        return self.get_product(current.product_id)

    def restock_product(self, product_id: str, quantity_to_add: int) -> Product:
        """Increase available stock for an existing product."""
        if (
            not isinstance(quantity_to_add, int)
            or isinstance(quantity_to_add, bool)
            or quantity_to_add <= 0
        ):
            raise InvalidQuantityError("Restock quantity must be a positive integer.")

        product = self.get_product(product_id)
        with self.db.connection() as conn:
            conn.execute(
                """
                UPDATE products
                SET stock = stock + ?, updated_at = datetime('now', 'localtime')
                WHERE UPPER(product_id) = ?
                """,
                (quantity_to_add, product.product_id),
            )
        return self.get_product(product.product_id)

    def delete_product(self, product_id: str) -> None:
        """Delete a product from the inventory database."""
        product = self.get_product(product_id)
        with self.db.connection() as conn:
            conn.execute(
                "DELETE FROM products WHERE UPPER(product_id) = ?",
                (product.product_id,),
            )

    def deduct_stock_in_conn(
        self,
        conn: sqlite3.Connection,
        items: list[tuple[str, int]],
    ) -> None:
        """Atomically verify and deduct stock for a batch of (product_id, qty) pairs."""
        for product_id, qty in items:
            if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
                raise InvalidQuantityError(f"Invalid purchase quantity {qty!r} for '{product_id}'.")

            row = conn.execute(
                "SELECT product_id, name, stock FROM products WHERE UPPER(product_id) = ?",
                (product_id.strip().upper(),),
            ).fetchone()
            if row is None:
                raise ProductNotFoundError(f"Product '{product_id}' does not exist.")

            available = int(row["stock"])
            if qty > available:
                raise InsufficientStockError(
                    f"Insufficient stock for '{row['name']}' ({row['product_id']}): "
                    f"requested {qty}, available {available}."
                )

            conn.execute(
                """
                UPDATE products
                SET stock = stock - ?, updated_at = datetime('now', 'localtime')
                WHERE product_id = ?
                """,
                (qty, row["product_id"]),
            )

    def get_low_stock_products(
        self,
        threshold: int = LOW_STOCK_DEFAULT_THRESHOLD,
    ) -> list[Product]:
        """Return products whose stock is at or below the specified threshold."""
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT p.product_id, p.name, c.name AS category_name, p.price, p.stock,
                       p.sku, p.created_at, p.updated_at
                FROM products p
                JOIN categories c ON c.category_id = p.category_id
                WHERE p.stock <= ?
                ORDER BY p.stock ASC, p.product_id ASC
                """,
                (int(threshold),),
            ).fetchall()
            return [self._row_to_product(r) for r in rows]
