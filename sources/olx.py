"""OLX Brazil scraper — extracts ads from __NEXT_DATA__ JSON."""
import asyncio
import json
import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from config_loader import SearchItem, config
from pipeline.proxy import proxy_rotator
from sources.base import ScrapedDeal, random_delay, is_junk

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
    """Extract numeric price from text like 'R$ 1.800'."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text)
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


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
            if proxy:
                proxy_rotator.report_success(proxy)
            return response.text
        logger.warning(f"OLX returned {response.status_code} for {url}")
        if proxy:
            proxy_rotator.report_failure(proxy)
        return None
    except Exception as e:
        logger.error(f"OLX fetch error: {e}")
        if proxy:
            proxy_rotator.report_failure(proxy)
        return None


def _parse_listings(html: str) -> list[ScrapedDeal]:
    """Parse OLX search results from __NEXT_DATA__ JSON."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", {"id": "__NEXT_DATA__"})

    if not script or not script.string:
        logger.warning("No __NEXT_DATA__ found in OLX response")
        return []

    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        logger.error("Failed to parse __NEXT_DATA__ JSON")
        return []

    ads = data.get("props", {}).get("pageProps", {}).get("ads", [])
    if not ads:
        return []

    deals = []
    for ad in ads:
        try:
            title = ad.get("subject") or ad.get("title") or ""
            price_str = ad.get("price") or ad.get("priceValue") or ""
            price = _parse_price(price_str)
            url = ad.get("url") or ad.get("friendlyUrl") or ""
            list_id = ad.get("listId")
            location = ad.get("location") or ""

            if not title or not price or not url:
                continue

            if is_junk(title, config.exclude_keywords):
                continue

            # First image thumbnail
            image_url = None
            images = ad.get("images")
            if images and len(images) > 0:
                img = images[0]
                if isinstance(img, dict):
                    image_url = img.get("original") or img.get("thumbnail")
                elif isinstance(img, str):
                    image_url = img

            deals.append(ScrapedDeal(
                source="olx",
                external_id=str(list_id) if list_id else url.split("-")[-1],
                title=title,
                price=price,
                url=url,
                location=location if location else None,
                image_url=image_url,
            ))
        except Exception as e:
            logger.debug(f"Failed to parse OLX ad: {e}")
            continue

    return deals


def _get_total_pages(html: str) -> int:
    """Extract total number of pages from __NEXT_DATA__."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script or not script.string:
        return 1
    try:
        data = json.loads(script.string)
        page_props = data.get("props", {}).get("pageProps", {})
        total_ads = page_props.get("totalOfAds", 0)
        page_size = page_props.get("pageSize", 50)
        if total_ads and page_size:
            return min((total_ads // page_size) + 1, MAX_PAGES)
    except Exception:
        pass
    return 1


def _matches_item(title: str, item: SearchItem) -> bool:
    """Check if deal title is actually relevant to the item being searched."""
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in item.keywords)


async def scrape_olx(item: SearchItem) -> list[ScrapedDeal]:
    """Scrape OLX for a specific item across all keywords."""
    all_deals: list[ScrapedDeal] = []
    seen_ids: set[str] = set()

    for keyword in item.keywords:
        # Fetch first page to get total
        url = _build_search_url(keyword, 1, item.max_price)
        logger.info(f"OLX: searching '{keyword}' page 1")

        html = _fetch_page(url)
        if not html:
            continue

        total_pages = _get_total_pages(html)
        deals = _parse_listings(html)

        for deal in deals:
            if deal.external_id not in seen_ids and _matches_item(deal.title, item):
                seen_ids.add(deal.external_id)
                all_deals.append(deal)

        # Fetch remaining pages
        for page in range(2, total_pages + 1):
            await random_delay(RATE_LIMIT, RATE_LIMIT + 2)
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

        await random_delay(RATE_LIMIT, RATE_LIMIT + 1)

    logger.info(f"OLX: found {len(all_deals)} deals for {item.name}")
    return all_deals


async def scrape_all_olx() -> dict[str, list[ScrapedDeal]]:
    """Scrape OLX for all configured items."""
    results: dict[str, list[ScrapedDeal]] = {}
    for item in config.items:
        deals = await scrape_olx(item)
        results[item.name] = deals
        await random_delay(3, 6)
    return results
