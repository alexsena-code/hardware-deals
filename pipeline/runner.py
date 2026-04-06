"""Orchestrates scraping across all sources and saves results."""
import asyncio
import logging

from config_loader import config
from models.database import SessionLocal
from pipeline.matcher import match_deal_to_item
from pipeline.storage import save_deals, record_price_snapshot
from sources.olx import scrape_olx
from sources.ebay import scrape_ebay

logger = logging.getLogger(__name__)


async def run_scrape():
    """Run a full scrape cycle for all items and sources."""
    logger.info("Starting scrape cycle...")
    db = SessionLocal()

    try:
        for item in config.items:
            olx_cfg = config.sources.get("olx")
            ebay_cfg = config.sources.get("ebay")

            # Scrape OLX
            if olx_cfg and olx_cfg.enabled:
                try:
                    olx_deals = await scrape_olx(item)
                    count = save_deals(db, olx_deals, item.name, item.category)
                    logger.info(f"OLX {item.name}: {count} deals saved")
                    if olx_deals:
                        record_price_snapshot(db, item.name, "olx")
                except Exception as e:
                    logger.error(f"OLX scrape failed for {item.name}: {e}")

            # Scrape eBay
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
