"""Auto-migrate: add missing columns/tables."""
import sys
sys.path.insert(0, "/opt/hardware-deals")

from models.database import engine, Base
from models.deals import StoreProduct, StoreProductHistory  # noqa: ensure models loaded
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
for table_name in ["store_products", "store_product_history"]:
    if table_name not in existing:
        Base.metadata.tables[table_name].create(engine)
        print(f"Created table: {table_name}")

print("Migration complete")
