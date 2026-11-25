"""Sales analytics, Pandas reporting, and Matplotlib visualization engine."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from database import BASE_DIR, DatabaseManager
from inventory import InventoryManager
from models import CATEGORIES, LOW_STOCK_DEFAULT_THRESHOLD, Product, SalesSummary

try:
    import pandas as pd  # type: ignore

    HAS_PANDAS = True
except ImportError:
    pd = None  # type: ignore
    HAS_PANDAS = False

try:
    import matplotlib  # type: ignore

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore

    HAS_MATPLOTLIB = True
except ImportError:
    plt = None  # type: ignore
    HAS_MATPLOTLIB = False


DEFAULT_REPORTS_DIR = BASE_DIR / "reports"


class SalesAnalytics:
    """Computes revenue KPIs, product/category insights, and visual sales charts."""

    def __init__(self, db: DatabaseManager, inventory: InventoryManager) -> None:
        self.db = db
        self.inventory = inventory

    def get_sales_summary(
        self,
        reference_date: Optional[str] = None,
    ) -> SalesSummary:
        """Compute daily revenue, monthly revenue, total transactions, and total discounts."""
        today_str = reference_date or datetime.now().strftime("%Y-%m-%d")
        month_prefix = today_str[:7]

        with self.db.connection() as conn:
            if HAS_PANDAS:
                df = pd.read_sql_query(
                    """
                    SELECT final_amount, discount_amount, created_at
                    FROM transactions
                    """,
                    conn,
                )
                if df.empty:
                    return SalesSummary(0.0, 0.0, 0.0, 0, 0.0, 0.0)

                df["date_only"] = df["created_at"].astype(str).str.slice(0, 10)
                df["month_only"] = df["created_at"].astype(str).str.slice(0, 7)

                daily_rev = float(df.loc[df["date_only"] == today_str, "final_amount"].sum())
                monthly_rev = float(df.loc[df["month_only"] == month_prefix, "final_amount"].sum())
                total_rev = float(df["final_amount"].sum())
                total_tx = int(len(df))
                total_disc = float(df["discount_amount"].sum())
                avg_val = total_rev / total_tx if total_tx > 0 else 0.0

                return SalesSummary(
                    daily_revenue=round(daily_rev, 2),
                    monthly_revenue=round(monthly_rev, 2),
                    total_revenue=round(total_rev, 2),
                    total_transactions=total_tx,
                    total_discounts=round(total_disc, 2),
                    average_order_value=round(avg_val, 2),
                )

            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN substr(created_at, 1, 10) = ? THEN final_amount ELSE 0 END), 0.0) AS daily_rev,
                    COALESCE(SUM(CASE WHEN substr(created_at, 1, 7) = ? THEN final_amount ELSE 0 END), 0.0) AS monthly_rev,
                    COALESCE(SUM(final_amount), 0.0) AS total_rev,
                    COUNT(*) AS total_tx,
                    COALESCE(SUM(discount_amount), 0.0) AS total_disc
                FROM transactions
                """,
                (today_str, month_prefix),
            ).fetchone()

            total_tx = int(row["total_tx"]) if row else 0
            total_rev = float(row["total_rev"]) if row else 0.0
            avg_val = total_rev / total_tx if total_tx > 0 else 0.0

            return SalesSummary(
                daily_revenue=round(float(row["daily_rev"]), 2) if row else 0.0,
                monthly_revenue=round(float(row["monthly_rev"]), 2) if row else 0.0,
                total_revenue=round(total_rev, 2),
                total_transactions=total_tx,
                total_discounts=round(float(row["total_disc"]), 2) if row else 0.0,
                average_order_value=round(avg_val, 2),
            )

    def get_best_selling_products(self, top_n: int = 10) -> list[dict[str, object]]:
        """Return the top-selling products ranked by units sold and revenue."""
        with self.db.connection() as conn:
            if HAS_PANDAS:
                df = pd.read_sql_query(
                    """
                    SELECT product_id, product_name, category_name, quantity, line_total
                    FROM transaction_items
                    """,
                    conn,
                )
                if df.empty:
                    return []
                grouped = (
                    df.groupby(["product_id", "product_name", "category_name"], as_index=False)
                    .agg(units_sold=("quantity", "sum"), revenue=("line_total", "sum"))
                    .sort_values(by=["units_sold", "revenue"], ascending=[False, False])
                    .head(int(top_n))
                )
                grouped["revenue"] = grouped["revenue"].round(2)
                return grouped.to_dict(orient="records")

            rows = conn.execute(
                """
                SELECT
                    product_id,
                    product_name,
                    category_name,
                    SUM(quantity) AS units_sold,
                    ROUND(SUM(line_total), 2) AS revenue
                FROM transaction_items
                GROUP BY product_id, product_name, category_name
                ORDER BY units_sold DESC, revenue DESC
                LIMIT ?
                """,
                (int(top_n),),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_category_wise_sales(self) -> list[dict[str, object]]:
        """Aggregate units sold and gross sales revenue per product category."""
        with self.db.connection() as conn:
            if HAS_PANDAS:
                df = pd.read_sql_query(
                    """
                    SELECT category_name, quantity, line_total
                    FROM transaction_items
                    """,
                    conn,
                )
                totals = {
                    cat: {"category_name": cat, "units_sold": 0, "revenue": 0.0}
                    for cat in CATEGORIES
                }
                if not df.empty:
                    grouped = df.groupby("category_name", as_index=False).agg(
                        units_sold=("quantity", "sum"),
                        revenue=("line_total", "sum"),
                    )
                    for record in grouped.to_dict(orient="records"):
                        cat = str(record["category_name"])
                        totals[cat] = {
                            "category_name": cat,
                            "units_sold": int(record["units_sold"]),
                            "revenue": round(float(record["revenue"]), 2),
                        }
                return sorted(
                    totals.values(),
                    key=lambda item: float(item["revenue"]),  # type: ignore[arg-type]
                    reverse=True,
                )

            rows = conn.execute(
                """
                SELECT
                    c.name AS category_name,
                    COALESCE(SUM(ti.quantity), 0) AS units_sold,
                    ROUND(COALESCE(SUM(ti.line_total), 0.0), 2) AS revenue
                FROM categories c
                LEFT JOIN transaction_items ti ON ti.category_name = c.name
                GROUP BY c.category_id, c.name
                ORDER BY revenue DESC, c.name ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_low_stock_products(
        self,
        threshold: int = LOW_STOCK_DEFAULT_THRESHOLD,
    ) -> list[Product]:
        """Identify products requiring replenishment."""
        return self.inventory.get_low_stock_products(threshold=threshold)

    def generate_sales_charts(
        self,
        output_dir: str | Path = DEFAULT_REPORTS_DIR,
    ) -> list[Path]:
        """Generate Matplotlib visualizations for category sales and best-selling items."""
        if not HAS_MATPLOTLIB:
            return []

        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        generated_files: list[Path] = []

        cat_sales = self.get_category_wise_sales()
        if cat_sales:
            categories = [str(row["category_name"]) for row in cat_sales]
            revenues = [float(row["revenue"]) for row in cat_sales]  # type: ignore[arg-type]

            fig, ax = plt.subplots(figsize=(9, 4.8))
            bars = ax.bar(categories, revenues, color="#0F172A", width=0.55)
            ax.set_title("Category-Wise Sales Revenue (INR)", fontsize=13, fontweight="bold")
            ax.set_ylabel("Revenue (INR)")
            ax.grid(axis="y", linestyle="--", alpha=0.35)
            for bar in bars:
                val = bar.get_height()
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    val,
                    f"{val:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )
            fig.tight_layout()
            cat_chart = target_dir / "category_sales.png"
            fig.savefig(cat_chart, dpi=150)
            plt.close(fig)
            generated_files.append(cat_chart)

        best_sellers = self.get_best_selling_products(top_n=8)
        if best_sellers:
            labels = [str(row["product_name"])[:28] for row in reversed(best_sellers)]
            units = [int(row["units_sold"]) for row in reversed(best_sellers)]  # type: ignore[arg-type]

            fig, ax = plt.subplots(figsize=(9.5, 5.0))
            ax.barh(labels, units, color="#0284C7")
            ax.set_title("Top Selling Products (Units Sold)", fontsize=13, fontweight="bold")
            ax.set_xlabel("Units Sold")
            ax.grid(axis="x", linestyle="--", alpha=0.35)
            fig.tight_layout()
            top_chart = target_dir / "best_selling_products.png"
            fig.savefig(top_chart, dpi=150)
            plt.close(fig)
            generated_files.append(top_chart)

        return generated_files

    def format_analytics_report(self) -> str:
        """Return a human-readable analytics summary for terminal reporting."""
        summary = self.get_sales_summary()
        cat_sales = self.get_category_wise_sales()
        best_sellers = self.get_best_selling_products(top_n=5)
        low_stock = self.get_low_stock_products()

        lines = [
            "=" * 72,
            "SALES ANALYTICS & INVENTORY HEALTH REPORT".center(72),
            "=" * 72,
            f"Daily Revenue        : Rs. {summary.daily_revenue:,.2f}",
            f"Monthly Revenue      : Rs. {summary.monthly_revenue:,.2f}",
            f"Total Revenue        : Rs. {summary.total_revenue:,.2f}",
            f"Total Transactions   : {summary.total_transactions}",
            f"Total Discounts Given: Rs. {summary.total_discounts:,.2f}",
            f"Average Order Value  : Rs. {summary.average_order_value:,.2f}",
            "-" * 72,
            "Category-Wise Sales:",
        ]
        for row in cat_sales:
            lines.append(
                f"  - {str(row['category_name']):<18} | Units: {int(row['units_sold']):>5} "
                f"| Revenue: Rs. {float(row['revenue']):>10,.2f}"
            )

        lines.append("-" * 72)
        lines.append("Top 5 Best-Selling Products:")
        if not best_sellers:
            lines.append("  No sales recorded yet.")
        else:
            for idx, item in enumerate(best_sellers, start=1):
                lines.append(
                    f"  {idx}. [{item['product_id']}] {item['product_name']} "
                    f"- {item['units_sold']} units (Rs. {float(item['revenue']):,.2f})"
                )

        lines.append("-" * 72)
        lines.append(f"Low-Stock Alerts (<= {LOW_STOCK_DEFAULT_THRESHOLD} units): {len(low_stock)} products")
        for prod in low_stock[:10]:
            lines.append(
                f"  ! {prod.product_id} | {prod.name[:34]:<34} | Stock: {prod.stock}"
            )
        lines.append("=" * 72)
        return "\n".join(lines)
