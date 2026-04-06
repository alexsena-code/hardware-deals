"""Scheduler — runs scraping at configured intervals."""
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config_loader import settings
from pipeline.runner import run_scrape

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_scrape,
        "interval",
        minutes=settings.scrape_interval_minutes,
        id="scrape_cycle",
    )
    scheduler.start()
    logger.info(f"Scheduler started — scraping every {settings.scrape_interval_minutes} minutes")

    # Run once immediately
    await run_scrape()

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
