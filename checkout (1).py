"""Shopping cart and discount evaluation logic for customer checkout."""

from __future__ import annotations

from typing import Optional

from database import DatabaseManager
from inventory import InventoryManager
from models import (
    AUTO_DISCOUNT_PERCENT,
    AUTO_DISCOUNT_THRESHOLD,
    CartItem,
    DiscountResult,
    InsufficientStockError,
    InvalidPromoCodeError,
    InvalidQuantityError,
    Product,
)


class ShoppingCart:
    """In-memory shopping cart with real-time stock validation and subtotal calculation."""

    def __init__(self, inventory: InventoryManager) -> None:
        self.inventory = inventory
        self._items: dict[str, CartItem] = {}

    def add_item(self, product_id: str, quantity: int = 1) -> CartItem:
        """Add a product to the cart or increment its quantity after validating stock."""
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise InvalidQuantityError("Quantity must be a positive integer.")

        product: Product = self.inventory.get_product(product_id)
        existing_qty = self._items[product.product_id].quantity if product.product_id in self._items else 0
        target_qty = existing_qty + quantity

        if target_qty > product.stock:
            raise InsufficientStockError(
                f"Cannot add {quantity}x '{product.name}'. "
                f"Requested total {target_qty}, available stock {product.stock}."
            )

        item = CartItem(product=product, quantity=target_qty)
        self._items[product.product_id] = item
        return item

    def update_quantity(self, product_id: str, quantity: int) -> Optional[CartItem]:
        """Set a new quantity for a cart item; quantity == 0 removes the item."""
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            raise InvalidQuantityError("Quantity must be zero or a positive integer.")

        clean_id = product_id.strip().upper()
        if clean_id not in self._items:
            raise KeyError(f"Product '{clean_id}' is not in the shopping cart.")

        if quantity == 0:
            del self._items[clean_id]
            return None

        product = self.inventory.get_product(clean_id)
        if quantity > product.stock:
            raise InsufficientStockError(
                f"Cannot set quantity to {quantity} for '{product.name}'; "
                f"only {product.stock} units available."
            )

        updated = CartItem(product=product, quantity=quantity)
        self._items[clean_id] = updated
        return updated

    def remove_item(self, product_id: str) -> None:
        """Remove a product from the shopping cart."""
        clean_id = product_id.strip().upper()
        if clean_id not in self._items:
            raise KeyError(f"Product '{clean_id}' is not in the shopping cart.")
        del self._items[clean_id]

    def clear(self) -> None:
        """Empty all items from the cart."""
        self._items.clear()

    @property
    def items(self) -> list[CartItem]:
        return list(self._items.values())

    @property
    def is_empty(self) -> bool:
        return len(self._items) == 0

    @property
    def total_units(self) -> int:
        return sum(item.quantity for item in self._items.values())

    @property
    def subtotal(self) -> float:
        return round(sum(item.line_total for item in self._items.values()), 2)


class DiscountEngine:
    """Evaluates manual promo codes and the automatic > ₹1000 discount (non-stackable)."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def list_active_promo_codes(self) -> list[dict[str, object]]:
        """Return all active promo codes configured in SQLite."""
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT code, discount_percent, description
                FROM promo_codes
                WHERE is_active = 1
                ORDER BY discount_percent DESC
                """
            ).fetchall()
            return [
                {
                    "code": row["code"],
                    "discount_percent": float(row["discount_percent"]),
                    "description": row["description"],
                }
                for row in rows
            ]

    def calculate_discount(
        self,
        subtotal: float,
        promo_code: Optional[str] = None,
    ) -> DiscountResult:
        """
        Calculate the single applicable discount for a transaction.

        Rule:
        - Only ONE discount may be applied per transaction.
        - If a valid manual promo code is supplied, it applies (SAVE10=10%, SAVE20=20%, WELCOME5=5%).
        - Otherwise, if subtotal > ₹1000, an automatic 5% discount applies.
        """
        if subtotal <= 0:
            return DiscountResult(
                discount_type="NONE",
                promo_code=None,
                discount_percent=0.0,
                discount_amount=0.0,
                description="No discount applicable",
            )

        clean_code = promo_code.strip().upper() if promo_code and promo_code.strip() else None
        if clean_code is not None:
            with self.db.connection() as conn:
                row = conn.execute(
                    """
                    SELECT code, discount_percent, description
                    FROM promo_codes
                    WHERE UPPER(code) = ? AND is_active = 1
                    """,
                    (clean_code,),
                ).fetchone()
            if row is None:
                raise InvalidPromoCodeError(
                    f"Promo code '{clean_code}' is invalid or inactive."
                )

            pct = float(row["discount_percent"])
            amount = round(subtotal * (pct / 100.0), 2)
            return DiscountResult(
                discount_type="PROMO",
                promo_code=row["code"],
                discount_percent=pct,
                discount_amount=amount,
                description=f"Promo code {row['code']} ({pct:.0f}% off)",
            )

        if subtotal > AUTO_DISCOUNT_THRESHOLD:
            amount = round(subtotal * (AUTO_DISCOUNT_PERCENT / 100.0), 2)
            return DiscountResult(
                discount_type="AUTO",
                promo_code=None,
                discount_percent=AUTO_DISCOUNT_PERCENT,
                discount_amount=amount,
                description=(
                    f"Automatic {AUTO_DISCOUNT_PERCENT:.0f}% discount "
                    f"(order above Rs.{AUTO_DISCOUNT_THRESHOLD:.0f})"
                ),
            )

        return DiscountResult(
            discount_type="NONE",
            promo_code=None,
            discount_percent=0.0,
            discount_amount=0.0,
            description="No discount applied",
        )
