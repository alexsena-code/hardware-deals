"""Auto-migrate: add missing columns/tables."""
import sys
sys.path.insert(0, "/opt/hardware-deals")

from models.database import engine, Base
from models.deals import StoreProduct, StoreProductHistory, BannedDeal  # noqa: ensure models loaded
from sqlalchemy import text, inspect

insp = inspect(engine)

# 1. search_items: add scrape_enabled
cols = {c["name"] for c in insp.get_columns("search_items")}
with engine.begin() as conn:
    if "scrape_enabled" not in cols:
        conn.execute(text("ALTER TABLE search_items ADD COLUMN scrape_enabled BOOLEAN DEFAULT true"))
        print("Added scrape_enabled column")

# 2. Create new tables if they don't exist (store_products, store_product_history)
existing = insp.get_table_names()
for table_name in ["store_products", "store_product_history", "banned_deals"]:
    if table_name not in existing:
        Base.metadata.tables[table_name].create(engine)
        print(f"Created table: {table_name}")

# 3. Widen tag columns if too short
for tbl, col in [("store_products", "tag"), ("store_product_history", "tag")]:
    if tbl in existing:
        cols_info = {c["name"]: c for c in insp.get_columns(tbl)}
        if col in cols_info:
            col_type = str(cols_info[col]["type"])
            if "100" not in col_type and "VARCHAR" in col_type.upper():
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE VARCHAR(100)"))
                    print(f"Widened {tbl}.{col} to VARCHAR(100)")

# 4. Add base_model columns
for tbl in ["store_products", "search_items"]:
    if tbl in existing:
        cols = {c["name"] for c in insp.get_columns(tbl)}
        if "base_model" not in cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN base_model VARCHAR(100)"))
                print(f"Added base_model to {tbl}")

# 5. Add image_urls JSON column to deals
if "deals" in existing:
    cols = {c["name"] for c in insp.get_columns("deals")}
    if "image_urls" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE deals ADD COLUMN image_urls JSON DEFAULT '[]'::json"))
            print("Added image_urls to deals")

# 6. Add allowed_item_categories JSON column to olx_categories
if "olx_categories" in existing:
    cols = {c["name"] for c in insp.get_columns("olx_categories")}
    if "allowed_item_categories" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE olx_categories ADD COLUMN allowed_item_categories JSON DEFAULT '[]'::json"))
            print("Added allowed_item_categories to olx_categories")

print("Migration complete")
