"""Domain models, configuration constants, and custom exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


CATEGORIES: tuple[str, ...] = (
    "Electronics",
    "Clothing",
    "Grocery",
    "Stationery",
    "Home Appliances",
)

DEFAULT_GST_RATE: float = 0.18
AUTO_DISCOUNT_THRESHOLD: float = 1000.0
AUTO_DISCOUNT_PERCENT: float = 5.0
LOW_STOCK_DEFAULT_THRESHOLD: int = 10

DEFAULT_PROMO_CODES: dict[str, tuple[float, str]] = {
    "SAVE10": (10.0, "Flat 10% off on entire cart"),
    "SAVE20": (20.0, "Flat 20% off on entire cart"),
    "WELCOME5": (5.0, "Welcome 5% discount"),
}


class RetailError(Exception):
    """Base exception for all Retail Shop Management System errors."""


class DatabaseError(RetailError):
    """Raised when a SQLite operation fails."""


class ValidationError(RetailError):
    """Raised when input validation fails."""


class ProductNotFoundError(RetailError):
    """Raised when a requested product ID does not exist."""


class DuplicateProductError(RetailError):
    """Raised when attempting to create a product with an existing ID or SKU."""


class InsufficientStockError(RetailError):
    """Raised when requested quantity exceeds available product stock."""


class InvalidQuantityError(ValidationError):
    """Raised when product quantity is not a positive integer."""


class InvalidPriceError(ValidationError):
    """Raised when product price is negative, zero, or non-numeric."""


class InvalidPromoCodeError(ValidationError):
    """Raised when a manual promo code is unknown or inactive."""


class EmptyCartError(RetailError):
    """Raised when attempting checkout with an empty shopping cart."""


class AuthenticationError(RetailError):
    """Raised when admin login credentials fail validation."""


@dataclass(slots=True)
class Category:
    """Represents a product category stored in SQLite."""

    category_id: Optional[int]
    name: str
    description: str = ""


@dataclass(slots=True)
class Product:
    """Represents a retail inventory product."""

    product_id: str
    name: str
    category: str
    price: float
    stock: int
    sku: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        self.product_id = self.product_id.strip().upper()
        self.name = self.name.strip()
        self.category = self.category.strip()
        self.sku = self.sku.strip().upper()
        if not self.product_id:
            raise ValidationError("Product ID cannot be empty.")
        if not self.name:
            raise ValidationError("Product name cannot be empty.")
        if self.category not in CATEGORIES:
            raise ValidationError(
                f"Invalid category '{self.category}'. Must be one of: {', '.join(CATEGORIES)}."
            )
        if not isinstance(self.price, (int, float)) or float(self.price) <= 0:
            raise InvalidPriceError(f"Price must be greater than zero, got {self.price!r}.")
        self.price = round(float(self.price), 2)
        if not isinstance(self.stock, int) or isinstance(self.stock, bool) or self.stock < 0:
            raise InvalidQuantityError(f"Stock must be a non-negative integer, got {self.stock!r}.")
        if not self.sku:
            raise ValidationError("SKU cannot be empty.")


@dataclass(slots=True)
class CartItem:
    """Represents a single line item inside a customer's shopping cart."""

    product: Product
    quantity: int

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
            raise InvalidQuantityError(f"Quantity must be a positive integer, got {self.quantity!r}.")

    @property
    def line_total(self) -> float:
        return round(self.product.price * self.quantity, 2)


@dataclass(slots=True)
class DiscountResult:
    """Captures the single non-stackable discount applied to a transaction."""

    discount_type: str  # 'PROMO', 'AUTO', or 'NONE'
    promo_code: Optional[str]
    discount_percent: float
    discount_amount: float
    description: str


@dataclass(slots=True)
class InvoiceLine:
    """Snapshot of a purchased item on a finalized invoice."""

    product_id: str
    product_name: str
    category: str
    unit_price: float
    quantity: int
    line_total: float


@dataclass(slots=True)
class Invoice:
    """Represents a completed billing invoice ready for display or PDF export."""

    bill_ref: str
    customer_name: str
    created_at: str
    items: list[InvoiceLine]
    subtotal: float
    discount_type: str
    promo_code: Optional[str]
    discount_percent: float
    discount_amount: float
    taxable_amount: float
    gst_rate: float
    gst_amount: float
    final_amount: float
    pdf_path: Optional[str] = None


@dataclass(slots=True)
class AdminAccount:
    """Represents an authenticated administrator account."""

    admin_id: int
    username: str
    role: str = "admin"
    last_login: Optional[str] = None


@dataclass(slots=True)
class SalesSummary:
    """Aggregated business KPIs returned by the analytics engine."""

    daily_revenue: float
    monthly_revenue: float
    total_revenue: float
    total_transactions: int
    total_discounts: float
    average_order_value: float
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
