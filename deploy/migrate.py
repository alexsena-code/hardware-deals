"""Auto-migrate: add missing columns to existing tables."""
import sys
sys.path.insert(0, "/opt/hardware-deals")

from models.database import engine
from sqlalchemy import text, inspect

insp = inspect(engine)

# search_items migrations
cols = {c["name"] for c in insp.get_columns("search_items")}
with engine.begin() as conn:
    if "scrape_enabled" not in cols:
        conn.execute(text("ALTER TABLE search_items ADD COLUMN scrape_enabled BOOLEAN DEFAULT true"))
        print("Added scrape_enabled column")
    else:
        print("scrape_enabled already exists")

print("Migration complete")
