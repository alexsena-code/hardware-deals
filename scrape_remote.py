"""Local scraper that sends results to the remote VPS API.
Run this on your PC (residential IP) — bypasses Cloudflare.
Results are POSTed to the VPS API which saves to PostgreSQL.
"""
import asyncio
import logging
import sys
import httpx

from config_loader import config, SearchItem
from sources.olx import scrape_olx
from sources.base import ScrapedDeal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

API_URL = "https://api.pathoftrade.net/hardware-api"


async def fetch_items() -> list[SearchItem]:
    """Fetch search items from VPS API (DB-managed items)."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            res = await client.get(f"{API_URL}/api/items")
            if res.status_code == 200:
                items_data = res.json()
                return [
                    SearchItem(
                        name=i["name"],
                        keywords=i["keywords"],
                        max_price=i["max_price"],
                        category=i["category"],
                        specs=i.get("specs", {}),
                    )
                    for i in items_data
                ]
        except Exception as e:
            logger.error(f"Failed to fetch items from API: {e}")

    # Fallback to local config
    logger.info("Using local config.yaml items")
    return config.items


async def send_deals(deals: list[ScrapedDeal], item_name: str, category: str) -> int:
    """Send scraped deals to VPS API."""
    if not deals:
        return 0

    sent = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for deal in deals:
            try:
                res = await client.post(
                    f"{API_URL}/api/deals/upsert",
                    json={
                        "source": deal.source,
                        "external_id": deal.external_id,
                        "item_name": item_name,
                        "title": deal.title,
                        "price": deal.price,
                        "url": deal.url,
                        "location": deal.location,
                        "image_url": deal.image_url,
                        "description": deal.description,
                        "category": category,
                    },
                )
                if res.status_code == 200:
                    sent += 1
            except Exception as e:
                logger.error(f"Failed to send deal: {e}")
    return sent


async def run():
    logger.info(f"Starting remote scrape → {API_URL}")

    # Fetch items from VPS (managed via dashboard)
    items = await fetch_items()
    logger.info(f"Scraping {len(items)} items")

    total_deals = 0
    for item in items:
        olx_cfg = config.sources.get("olx")
        if not olx_cfg or not olx_cfg.enabled:
            continue

        try:
            deals = await scrape_olx(item)
            if deals:
                sent = await send_deals(deals, item.name, item.category)
                total_deals += sent
                logger.info(f"{item.name}: {len(deals)} found, {sent} sent to API")
            else:
                logger.info(f"{item.name}: 0 deals found")
        except Exception as e:
            logger.error(f"Scrape failed for {item.name}: {e}")

    logger.info(f"Done! Total: {total_deals} deals sent to VPS")


if __name__ == "__main__":
    asyncio.run(run())
