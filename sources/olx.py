"""OLX Brazil scraper using curl_cffi to bypass Cloudflare."""
import asyncio
import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from config_loader import SearchItem, config
from pipeline.proxy import proxy_rotator
from sources.base import ScrapedDeal, random_delay, random_ua

logger = logging.getLogger(__name__)

BASE_URL = "https://www.olx.com.br"
OLX_RATE_LIMIT = config.sources.get("olx")
RATE_LIMIT = OLX_RATE_LIMIT.rate_limit_seconds if OLX_RATE_LIMIT else 2.0
MAX_PAGES = OLX_RATE_LIMIT.max_pages if OLX_RATE_LIMIT else 5


def _build_search_url(keyword: str, page: int = 1, max_price: int | None = None) -> str:
    url = f"{BASE_URL}/informatica?q={quote_plus(keyword)}"
    if page > 1:
        url += f"&o={page}"
    if max_price:
        url += f"&pe={max_price}"
    return url


def _parse_price(text: str) -> float | None:
    """Extract numeric price from text like 'R$ 1.200'."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text)
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_id_from_url(url: str) -> str:
    """Extract OLX ad ID from URL."""
    # OLX URLs end with -XXXXXXXXXX
    match = re.search(r"-(\d{8,12})$", url.rstrip("/"))
    if match:
        return match.group(1)
    return url.split("/")[-1]


def _fetch_page(url: str) -> str | None:
    """Fetch a page using curl_cffi with Chrome impersonation."""
    proxy = proxy_rotator.get_next()
    proxies = {"https": proxy, "http": proxy} if proxy else None

    try:
        response = curl_requests.get(
            url,
            impersonate="chrome",
            proxies=proxies,
            timeout=15,
            headers={
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if response.status_code == 200:
            return response.text
        logger.warning(f"OLX returned {response.status_code} for {url}")
        return None
    except Exception as e:
        logger.error(f"OLX fetch error: {e}")
        return None


def _parse_listings(html: str) -> list[ScrapedDeal]:
    """Parse OLX search results page."""
    soup = BeautifulSoup(html, "html.parser")
    deals = []

    # Try stable data attributes first
    cards = soup.select("a[data-lurker-detail='list_id']")

    if not cards:
        # Fallback: try data-cy attribute (OLX platform standard)
        cards = soup.select("[data-cy='l-card']")

    if not cards:
        # Last fallback: find all ad links by URL pattern
        cards = soup.find_all("a", href=re.compile(r"/item/"))

    for card in cards:
        try:
            # Extract URL
            if card.name == "a":
                url = card.get("href", "")
            else:
                link = card.find("a", href=True)
                url = link["href"] if link else ""

            if not url or "/item/" not in url:
                continue
            if not url.startswith("http"):
                url = BASE_URL + url

            # Extract title
            title_el = card.find("h2") or card.find("h3")
            title = title_el.get_text(strip=True) if title_el else ""

            # Extract price - look for R$ pattern
            price = None
            for span in card.find_all("span"):
                text = span.get_text(strip=True)
                if "R$" in text:
                    price = _parse_price(text)
                    if price and price > 0:
                        break

            if not title or not price:
                continue

            # Extract location
            location = None
            loc_el = card.find("span", {"data-testid": "location-date"})
            if not loc_el:
                # Fallback: look for location-like spans (city, state pattern)
                for span in card.find_all("span"):
                    text = span.get_text(strip=True)
                    if re.search(r"[A-Z]{2}$", text) and "R$" not in text:
                        location = text
                        break

            # Extract image
            img = card.find("img")
            image_url = img.get("src") or img.get("data-src") if img else None

            external_id = _extract_id_from_url(url)

            deals.append(ScrapedDeal(
                source="olx",
                external_id=external_id,
                title=title,
                price=price,
                url=url,
                location=location,
                image_url=image_url,
            ))
        except Exception as e:
            logger.debug(f"Failed to parse OLX card: {e}")
            continue

    return deals


async def scrape_olx(item: SearchItem) -> list[ScrapedDeal]:
    """Scrape OLX for a specific item across all keywords."""
    all_deals: list[ScrapedDeal] = []
    seen_ids: set[str] = set()

    for keyword in item.keywords:
        for page in range(1, MAX_PAGES + 1):
            url = _build_search_url(keyword, page, item.max_price)
            logger.info(f"OLX: searching '{keyword}' page {page}")

            html = _fetch_page(url)
            if not html:
                break

            deals = _parse_listings(html)
            if not deals:
                break

            for deal in deals:
                if deal.external_id not in seen_ids:
                    seen_ids.add(deal.external_id)
                    all_deals.append(deal)

            await random_delay(RATE_LIMIT, RATE_LIMIT + 2)

    logger.info(f"OLX: found {len(all_deals)} deals for {item.name}")
    return all_deals


async def scrape_all_olx() -> dict[str, list[ScrapedDeal]]:
    """Scrape OLX for all configured items."""
    results: dict[str, list[ScrapedDeal]] = {}
    for item in config.items:
        deals = await scrape_olx(item)
        results[item.name] = deals
        await random_delay(3, 6)  # longer pause between items
    return results
