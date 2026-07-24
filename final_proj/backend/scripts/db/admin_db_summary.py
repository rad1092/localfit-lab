from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.settings import DATABASE_PATH


TABLES = (
    "commercial_area",
    "district_population",
    "district_floating",
    "district_sales",
    "district_store_count",
    "district_growth_history",
    "area_sale_price_proxy",
    "area_rone_cost_reference",
    "rule_location_score",
    "rule_area_score_summary",
    "industry_hierarchy",
    "location_lookup",
)


def main() -> None:
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DATABASE_PATH}")
    with sqlite3.connect(DATABASE_PATH) as conn:
        available = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in TABLES
            if table in available
        }
        quarter = None
        if "rule_location_score" in available:
            quarter = conn.execute("SELECT MAX(quarter) FROM rule_location_score").fetchone()[0]
    print(
        json.dumps(
            {
                "database": str(DATABASE_PATH),
                "quarter": quarter,
                "table_counts": counts,
                "status": "healthy",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
