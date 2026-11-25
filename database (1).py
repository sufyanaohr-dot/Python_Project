"""SQLite database management, schema initialization, security helpers, and 1,000-product seeding."""

from __future__ import annotations

import hashlib
import hmac
import os
import random
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from models import CATEGORIES, DEFAULT_PROMO_CODES, DatabaseError


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "retail_store.db"


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256 with a cryptographic salt."""
    if not password:
        raise ValueError("Password cannot be empty.")
    salt_hex = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_hex.encode("utf-8"),
        120_000,
    ).hex()
    return f"{salt_hex}${derived}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored 'salt$hash' string in constant time."""
    if not password or "$" not in stored_hash:
        return False
    salt_hex, expected_digest = stored_hash.split("$", 1)
    candidate = hash_password(password, salt=salt_hex).split("$", 1)[1]
    return hmac.compare_digest(candidate, expected_digest)


# Rich catalog templates per category used to deterministically synthesize 200 distinct products each
_CATALOG_BLUEPRINTS: dict[str, dict[str, object]] = {
    "Electronics": {
        "sku_prefix": "ELEC",
        "brands": [
            "Sony", "Samsung", "Logitech", "Anker", "JBL",
            "Dell", "TP-Link", "Lenovo", "boAt", "Philips",
        ],
        "items": [
            "Wireless Mouse", "Mechanical Keyboard", "Bluetooth Earbuds", "USB-C Hub",
            "Portable SSD 1TB", "Smart LED Bulb", "Webcam 1080p", "Power Bank 20000mAh",
            "HDMI 2.1 Cable", "Noise-Cancelling Headphones", "Wi-Fi 6 Router", "24-inch IPS Monitor",
            "Surge Protector", "Wireless Charger Pad", "Bluetooth Speaker", "Gaming Headset",
            "Laptop Cooling Pad", "Memory Card 128GB", "Smartwatch Band", "Ethernet Switch 8-Port",
        ],
        "variants": [
            "Pro", "Lite", "Max", "Plus", "Studio",
            "Core", "Ultra", "Compact", "Air", "Edition",
        ],
        "price_range": (299.0, 24999.0),
    },
    "Clothing": {
        "sku_prefix": "CLTH",
        "brands": [
            "Levi's", "Allen Solly", "Raymond", "Van Heusen", "Nike",
            "Puma", "Fabindia", "Wrangler", "Peter England", "U.S. Polo",
        ],
        "items": [
            "Oxford Cotton Shirt", "Slim Fit Chinos", "Crewneck T-Shirt", "Denim Jeans",
            "Fleece Hoodie", "Polo Collar Tee", "Kurta Tunic", "Running Shorts",
            "Knitted Sweater", "Formal Trousers", "Windbreaker Jacket", "Linen Casual Shirt",
            "Track Pants", "Canvas Belt", "Cotton Socks Pack", "Graphic Sweatshirt",
            "Tailored Blazer", "Cargo Joggers", "Thermal Innerwear", "Sports Cap",
        ],
        "variants": [
            "Navy S", "Charcoal M", "Olive L", "Ivory XL", "Indigo M",
            "Slate L", "Crimson S", "Sandstone M", "Jet Black L", "Forest XL",
        ],
        "price_range": (249.0, 5499.0),
    },
    "Grocery": {
        "sku_prefix": "GROC",
        "brands": [
            "Tata", "Aashirvaad", "Fortune", "Amul", "Organic India",
            "MTR", "Dabur", "Saffola", "24 Mantra", "Britannia",
        ],
        "items": [
            "Basmati Rice", "Whole Wheat Atta", "Cold-Pressed Mustard Oil", "Toor Dal",
            "Roasted Almonds", "Assam Leaf Tea", "Filter Coffee Powder", "Wildflower Honey",
            "Rolled Oats", "Turmeric Powder", "Red Chilli Flakes", "Ghee Clarified Butter",
            "Peanut Butter Creamy", "Multigrain Biscuits", "Dark Chocolate Bar", "Chia Seeds",
            "Poha Flattened Rice", "Pink Himalayan Salt", "Apple Cider Vinegar", "Green Tea Bags",
        ],
        "variants": [
            "250g", "500g", "1kg", "2kg", "5kg",
            "200ml", "500ml", "1L", "Family Pack", "Value Jar",
        ],
        "price_range": (45.0, 1490.0),
    },
    "Stationery": {
        "sku_prefix": "STAT",
        "brands": [
            "Classmate", "Parker", "Camlin", "Faber-Castell", "Cello",
            "JK Copier", "Pidilite", "Staedtler", "Kokuyo", "Post-it",
        ],
        "items": [
            "Ruled Spiral Notebook", "Gel Ink Pen Set", "A4 Printer Paper Ream", "Highlighter Pack",
            "Sticky Notes Cube", "Scientific Calculator", "Acrylic Paint Set", "Mechanical Pencil",
            "Heavy-Duty Stapler", "Document Binder Folder", "Whiteboard Marker Set", "Geometry Box",
            "Desk Organizer Tray", "Correction Tape", "Graph Pad A4", "Fountain Pen",
            "Paper Clips Box", "Glue Stick Pack", "Lamination Pouches", "Index Tab Dividers",
        ],
        "variants": [
            "Pack of 5", "Pack of 10", "Single Unit", "500 Sheets", "200 Pages",
            "Fine Tip", "Medium Tip", "Assorted Colors", "Executive", "Student Pack",
        ],
        "price_range": (25.0, 1850.0),
    },
    "Home Appliances": {
        "sku_prefix": "HAPP",
        "brands": [
            "Philips", "Bajaj", "Prestige", "Havells", "LG",
            "IFB", "Morphy Richards", "Crompton", "Usha", "Voltas",
        ],
        "items": [
            "Electric Kettle", "Mixer Grinder", "Steam Iron", "Induction Cooktop",
            "Air Fryer", "Toaster 2-Slice", "Ceiling Fan", "Tower Air Cooler",
            "Vacuum Cleaner", "Water Purifier Cartridge", "Hand Blender", "Room Heater",
            "Rice Cooker", "Coffee Maker", "Sandwich Griller", "Table Fan",
            "LED Desk Lamp", "Garment Steamer", "Microwave Safe Bowl Set", "Juicer Extractor",
        ],
        "variants": [
            "750W", "1000W", "1200W", "1500W", "2000W",
            "1.5L", "2.0L", "4.5L", "Compact", "Heavy Duty",
        ],
        "price_range": (499.0, 18999.0),
    },
}


class DatabaseManager:
    """Encapsulates SQLite connection lifecycle, schema DDL, and seed initialization."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize_database()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Provide a transactional SQLite connection with foreign keys enabled."""
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            if conn is not None:
                conn.rollback()
            raise DatabaseError(f"Database operation failed: {exc}") from exc
        finally:
            if conn is not None:
                conn.close()

    def initialize_database(self) -> None:
        """Create tables and seed categories, 1,000 products, promo codes, and default admin."""
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS products (
                    product_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category_id INTEGER NOT NULL,
                    price REAL NOT NULL CHECK (price > 0),
                    stock INTEGER NOT NULL CHECK (stock >= 0),
                    sku TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    FOREIGN KEY (category_id) REFERENCES categories(category_id)
                        ON UPDATE CASCADE
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
                CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);

                CREATE TABLE IF NOT EXISTS admins (
                    admin_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'admin',
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    last_login TEXT
                );

                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    discount_percent REAL NOT NULL CHECK (discount_percent > 0 AND discount_percent <= 100),
                    description TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bill_ref TEXT NOT NULL UNIQUE,
                    customer_name TEXT NOT NULL DEFAULT 'Walk-in Customer',
                    subtotal REAL NOT NULL CHECK (subtotal >= 0),
                    discount_type TEXT NOT NULL,
                    promo_code TEXT,
                    discount_percent REAL NOT NULL DEFAULT 0,
                    discount_amount REAL NOT NULL DEFAULT 0,
                    taxable_amount REAL NOT NULL CHECK (taxable_amount >= 0),
                    gst_rate REAL NOT NULL CHECK (gst_rate >= 0),
                    gst_amount REAL NOT NULL CHECK (gst_amount >= 0),
                    final_amount REAL NOT NULL CHECK (final_amount >= 0),
                    pdf_path TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at);

                CREATE TABLE IF NOT EXISTS transaction_items (
                    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id INTEGER NOT NULL,
                    product_id TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    category_name TEXT NOT NULL,
                    unit_price REAL NOT NULL CHECK (unit_price > 0),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    line_total REAL NOT NULL CHECK (line_total >= 0),
                    FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_tx_items_tx_id ON transaction_items(transaction_id);
                """
            )

            self._seed_categories(conn)
            self._seed_promo_codes(conn)
            self._seed_default_admin(conn)
            self._seed_initial_products(conn)

    def _seed_categories(self, conn: sqlite3.Connection) -> None:
        descriptions = {
            "Electronics": "Consumer electronics, peripherals, networking, and accessories",
            "Clothing": "Apparel, casualwear, formalwear, and activewear",
            "Grocery": "Pantry staples, beverages, grains, oils, and packaged foods",
            "Stationery": "Office supplies, writing instruments, paper, and art materials",
            "Home Appliances": "Kitchen and household electrical appliances",
        }
        for name in CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, description) VALUES (?, ?)",
                (name, descriptions.get(name, "")),
            )

    def _seed_promo_codes(self, conn: sqlite3.Connection) -> None:
        for code, (percent, desc) in DEFAULT_PROMO_CODES.items():
            conn.execute(
                """
                INSERT OR IGNORE INTO promo_codes (code, discount_percent, description, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (code, percent, desc),
            )

    def _seed_default_admin(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM admins").fetchone()
        if row and row["cnt"] == 0:
            default_user = os.getenv("RETAIL_ADMIN_USER", "admin").strip() or "admin"
            default_pass = os.getenv("RETAIL_ADMIN_PASS", "Admin@123")
            conn.execute(
                "INSERT INTO admins (username, password_hash, role) VALUES (?, ?, 'superadmin')",
                (default_user, hash_password(default_pass)),
            )

    def _seed_initial_products(self, conn: sqlite3.Connection) -> None:
        """Populate exactly 1,000 products (200 per category) on first database creation."""
        existing = conn.execute("SELECT COUNT(*) AS cnt FROM products").fetchone()
        if existing and existing["cnt"] > 0:
            return

        cat_rows = conn.execute("SELECT category_id, name FROM categories").fetchall()
        cat_map = {row["name"]: row["category_id"] for row in cat_rows}

        rng = random.Random(2026)
        records: list[tuple[str, str, int, float, int, str]] = []
        global_index = 1

        for category_name in CATEGORIES:
            blueprint = _CATALOG_BLUEPRINTS[category_name]
            brands: list[str] = blueprint["brands"]  # type: ignore[assignment]
            items: list[str] = blueprint["items"]  # type: ignore[assignment]
            variants: list[str] = blueprint["variants"]  # type: ignore[assignment]
            sku_prefix: str = blueprint["sku_prefix"]  # type: ignore[assignment]
            min_price, max_price = blueprint["price_range"]  # type: ignore[misc]
            cat_id = cat_map[category_name]

            # Build 200 unique combinations (10 brands * 20 items, cycling variants cleanly)
            per_cat_count = 0
            for item_idx, item_name in enumerate(items):
                for brand_idx, brand_name in enumerate(brands):
                    per_cat_count += 1
                    variant = variants[(item_idx + brand_idx) % len(variants)]
                    product_id = f"PRD-{global_index:04d}"
                    sku = f"SKU-{sku_prefix}-{per_cat_count:04d}"
                    full_name = f"{brand_name} {item_name} ({variant})"

                    raw_price = rng.uniform(min_price, max_price)
                    price = round(raw_price / 5.0) * 5.0 - 1.0 if raw_price > 100 else round(raw_price, 2)
                    price = max(15.0, round(float(price), 2))

                    # Include realistic low-stock items (~6%) and healthy stock items
                    if per_cat_count % 17 == 0:
                        stock = rng.randint(2, 8)
                    else:
                        stock = rng.randint(15, 160)

                    records.append((product_id, full_name, cat_id, price, stock, sku))
                    global_index += 1

        conn.executemany(
            """
            INSERT INTO products (product_id, name, category_id, price, stock, sku)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            records,
        )
