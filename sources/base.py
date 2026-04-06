import random
import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]


@dataclass
class ScrapedDeal:
    source: str
    external_id: str
    title: str
    price: float
    url: str
    location: str | None = None
    image_url: str | None = None
    description: str | None = None


def random_ua() -> str:
    return random.choice(USER_AGENTS)


async def random_delay(min_s: float = 1.5, max_s: float = 4.0):
    await asyncio.sleep(random.uniform(min_s, max_s))
