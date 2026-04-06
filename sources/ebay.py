"""eBay scraper using httpx (server-rendered HTML, no Cloudflare)."""
import asyncio
import logging
import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from config_loader import SearchItem, config
from pipeline.proxy import proxy_rotator
from sources.base import ScrapedDeal, random_delay, random_ua

logger = logging.getLogger(__name__)

EBAY_RATE_LIMIT_CFG = config.sources.get("ebay")
RATE_LIMIT = EBAY_RATE_LIMIT_CFG.rate_limit_seconds if EBAY_RATE_LIMIT_CFG else 1.0
MAX_PAGES = EBAY_RATE_LIMIT_CFG.max_pages if EBAY_RATE_LIMIT_CFG else 3

# eBay search parameters
EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"
CATEGORY_GPUS = "27386"


def _build_search_url(keyword: str, page: int = 1, max_price: int | None = None) -> str:
    params = f"?_nkw={quote_plus(keyword)}&_pgn={page}&LH_ItemCondition=4&_ipg=60"
    if max_price:
        params += f"&_udhi={max_price}"
    return EBAY_SEARCH_URL + params


def _parse_price(text: str) -> float | None:
    """Parse eBay price (USD or BRL)."""
    cleaned = re.sub(r"[^\d,.]", "", text)
    # eBay US format: 1,200.00
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _get_headers() -> dict:
    return {
        "User-Agent": random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
    }


async def _fetch_page(client: httpx.AsyncClient, url: str) -> str | None:
    proxy_url = proxy_rotator.get_next()
    try:
        response = await client.get(
            url,
            headers=_get_headers(),
            timeout=15,
            follow_redirects=True,
        )
        if response.status_code == 200:
            return response.text
        logger.warning(f"eBay returned {response.status_code} for {url}")
        return None
    except Exception as e:
        logger.error(f"eBay fetch error: {e}")
        return None


def _parse_listings(html: str) -> list[ScrapedDeal]:
    """Parse eBay search results."""
    soup = BeautifulSoup(html, "html.parser")
    deals = []

    # Try both layouts
    cards = soup.select("li.s-item")
    if not cards:
        cards = soup.select("li.s-card")

    for i, card in enumerate(cards):
        try:
            # Skip first s-item (often a dummy)
            if i == 0 and card.select_one(".s-item__title"):
                title_text = card.select_one(".s-item__title").get_text(strip=True)
                if title_text == "Shop on eBay" or not title_text:
                    continue

            # Title
            title_el = card.select_one(".s-item__title") or card.select_one(".s-card__title")
            title = title_el.get_text(strip=True) if title_el else ""

            # Price
            price_el = card.select_one(".s-item__price") or card.select_one(".s-card__price")
            price_text = price_el.get_text(strip=True) if price_el else ""
            price = _parse_price(price_text)

            # URL
            link_el = card.select_one("a.s-item__link") or card.select_one("a.su-link") or card.find("a", href=True)
            url = link_el["href"] if link_el else ""

            if not title or not price or not url:
                continue

            # Image
            img = card.select_one(".s-item__image-img") or card.select_one(".s-card__image img")
            image_url = None
            if img:
                image_url = img.get("src") or img.get("data-src")

            # Extract eBay item ID from URL
            id_match = re.search(r"/itm/(\d+)", url)
            external_id = id_match.group(1) if id_match else url

            # Location
            loc_el = card.select_one(".s-item__location")
            location = loc_el.get_text(strip=True) if loc_el else None

            deals.append(ScrapedDeal(
                source="ebay",
                external_id=str(external_id),
                title=title,
                price=price,
                url=url,
                location=location,
                image_url=image_url,
            ))
        except Exception as e:
            logger.debug(f"Failed to parse eBay card: {e}")
            continue

    return deals


async def scrape_ebay(item: SearchItem) -> list[ScrapedDeal]:
    """Scrape eBay for a specific item."""
    all_deals: list[ScrapedDeal] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient() as client:
        for keyword in item.keywords:
            for page in range(1, MAX_PAGES + 1):
                url = _build_search_url(keyword, page, item.max_price)
                logger.info(f"eBay: searching '{keyword}' page {page}")

                html = await _fetch_page(client, url)
                if not html:
                    break

                deals = _parse_listings(html)
                if not deals:
                    break

                for deal in deals:
                    if deal.external_id not in seen_ids:
                        seen_ids.add(deal.external_id)
                        all_deals.append(deal)

                await random_delay(RATE_LIMIT, RATE_LIMIT + 1.5)

    logger.info(f"eBay: found {len(all_deals)} deals for {item.name}")
    return all_deals


async def scrape_all_ebay() -> dict[str, list[ScrapedDeal]]:
    """Scrape eBay for all configured items."""
    results: dict[str, list[ScrapedDeal]] = {}
    for item in config.items:
        deals = await scrape_ebay(item)
        results[item.name] = deals
        await random_delay(2, 4)
    return results
