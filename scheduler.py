"""Scheduler — triggers scraping at configured intervals via API."""
import asyncio
import logging

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config_loader import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_URL = f"http://localhost:{settings.api_port}"


async def trigger_scrape():
    """Call the API scrape endpoint — dispatches to WebSocket worker if connected, otherwise runs locally."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{API_URL}/api/scrape")
            if resp.status_code == 200:
                data = resp.json()
                logger.info("Scrape triggered: %s (worker=%s, items=%s)",
                            data.get("status"), data.get("worker"), data.get("items"))
            else:
                logger.error("Scrape trigger failed: HTTP %d", resp.status_code)
    except Exception as e:
        logger.error("Scrape trigger error: %s", e)


async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        trigger_scrape,
        "interval",
        minutes=settings.scrape_interval_minutes,
        id="scrape_cycle",
    )
    scheduler.start()
    logger.info("Scheduler started — triggering scrape every %d minutes via API", settings.scrape_interval_minutes)

    # Run once immediately
    await trigger_scrape()

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
