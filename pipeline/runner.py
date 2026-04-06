"""Orchestrates scraping across all sources and saves results."""
import asyncio
import logging

from sqlalchemy import select

from config_loader import config, SearchItem as ConfigSearchItem
from models.database import SessionLocal
from models.deals import SearchItem
from pipeline.storage import save_deals, record_price_snapshot
from sources.olx import scrape_olx
from sources.ebay import scrape_ebay

logger = logging.getLogger(__name__)


def get_items_from_db() -> list[ConfigSearchItem]:
    """Load active search items from database."""
    db = SessionLocal()
    try:
        db_items = db.execute(
            select(SearchItem).where(SearchItem.is_active == True)
        ).scalars().all()

        return [
            ConfigSearchItem(
                name=i.name,
                keywords=i.keywords,
                max_price=i.max_price,
                category=i.category,
                specs=i.specs or {},
            )
            for i in db_items
        ]
    finally:
        db.close()


async def run_scrape():
    """Run a full scrape cycle for all items and sources."""
    logger.info("Starting scrape cycle...")
    db = SessionLocal()

    # Load items from DB (falls back to config if DB empty)
    items = get_items_from_db()
    if not items:
        items = config.items
        logger.info("No items in DB, using config.yaml")

    try:
        for item in items:
            olx_cfg = config.sources.get("olx")
            ebay_cfg = config.sources.get("ebay")

            if olx_cfg and olx_cfg.enabled:
                try:
                    olx_deals = await scrape_olx(item)
                    count = save_deals(db, olx_deals, item.name, item.category)
                    logger.info(f"OLX {item.name}: {count} deals saved")
                    if olx_deals:
                        record_price_snapshot(db, item.name, "olx")
                except Exception as e:
                    logger.error(f"OLX scrape failed for {item.name}: {e}")

            if ebay_cfg and ebay_cfg.enabled:
                try:
                    ebay_deals = await scrape_ebay(item)
                    count = save_deals(db, ebay_deals, item.name, item.category)
                    logger.info(f"eBay {item.name}: {count} deals saved")
                    if ebay_deals:
                        record_price_snapshot(db, item.name, "ebay")
                except Exception as e:
                    logger.error(f"eBay scrape failed for {item.name}: {e}")

        logger.info("Scrape cycle complete.")
    finally:
        db.close()
