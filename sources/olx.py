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
OLX_CFG = config.sources.get("olx")
RATE_LIMIT = OLX_CFG.rate_limit_seconds if OLX_CFG else 2.0
MAX_PAGES = OLX_CFG.max_pages if OLX_CFG else 5
MIN_PRICE = OLX_CFG.min_price if OLX_CFG else 50


def _get_search_paths() -> list[str]:
    """Load active OLX categories from DB, fallback to config."""
    try:
        from models.database import SessionLocal
        from models.deals import OlxCategory
        from sqlalchemy import select
        db = SessionLocal()
        cats = db.execute(
            select(OlxCategory.path).where(OlxCategory.is_active == True)
        ).scalars().all()
        db.close()
        if cats:
            return list(cats)
    except Exception:
        pass
    return OLX_CFG.search_paths if OLX_CFG else ["/informatica"]


def _build_search_url(keyword: str, page: int = 1, max_price: int | None = None, path: str = "/informatica") -> str:
    url = f"{BASE_URL}{path}?q={quote_plus(keyword)}"
    if page > 1:
        url += f"&o={page}"
    if max_price:
        url += f"&pe={max_price}"
    url += f"&ps={MIN_PRICE}"  # Min price to filter junk
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

            # All image URLs
            image_urls: list[str] = []
            images = ad.get("images")
            if images:
                for img in images:
                    url_img = None
                    if isinstance(img, dict):
                        url_img = img.get("original") or img.get("thumbnail")
                    elif isinstance(img, str):
                        url_img = img
                    if url_img:
                        image_urls.append(url_img)

            deals.append(ScrapedDeal(
                source="olx",
                external_id=str(list_id) if list_id else url.split("-")[-1],
                title=title,
                price=price,
                url=url,
                location=location if location else None,
                image_url=image_urls[0] if image_urls else None,
                image_urls=image_urls,
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
    """Check if deal title is relevant to the item.

    Strategy:
    1. Normalize: lowercase + collapse non-alphanumeric to single space
       "Rtx 2080-Ti" -> "rtx 2080 ti", "Rtx2080Ti" -> "rtx2080ti"
    2. Match keyword with digit-aware word boundaries
       (digit-letter boundary IS a boundary for our purposes)
    3. Reject false positives: "1080" preceded by FullHD-like context
    """
    import re

    # Normalize: lowercase, replace special chars with space
    title_lower = title.lower()
    # Insert spaces between letter-digit transitions: rtx2080ti -> rtx 2080 ti
    title_norm = re.sub(r"([a-z])(\d)", r"\1 \2", title_lower)
    title_norm = re.sub(r"(\d)([a-z])", r"\1 \2", title_norm)
    # Collapse non-alphanumeric to space
    title_norm = re.sub(r"[^a-z0-9]+", " ", title_norm).strip()

    # Reject obvious false positives for resolution numbers
    resolution_traps = ["full hd", "1920x1080", "1920 x 1080", "1366x768", "lcd", "monitor", "tela", "notebook"]
    title_for_trap = title_lower.replace(" ", "")
    has_resolution_context = any(t.replace(" ", "") in title_for_trap for t in resolution_traps)

    matched = False
    for kw in item.keywords:
        kw_lower = kw.lower().strip()
        # Normalize the keyword the same way
        kw_norm = re.sub(r"([a-z])(\d)", r"\1 \2", kw_lower)
        kw_norm = re.sub(r"(\d)([a-z])", r"\1 \2", kw_norm)
        kw_norm = re.sub(r"[^a-z0-9]+", " ", kw_norm).strip()
        if not kw_norm:
            continue

        # Word-boundary match on normalized strings (now space-separated tokens)
        pattern = r"\b" + re.escape(kw_norm) + r"\b"
        if re.search(pattern, title_norm):
            # Reject if it's likely a resolution false positive
            # (e.g. keyword "1080" matching in "Full HD 1080")
            if has_resolution_context and kw_norm.strip() in ("1080", "1080p", "1366", "768"):
                continue
            matched = True
            break

    if not matched:
        logger.debug(f"FILTERED OUT: '{title}' — no keyword match for {item.name} {item.keywords}")
    return matched


async def scrape_olx(item: SearchItem, search_paths: list[str] | None = None) -> list[ScrapedDeal]:
    """Scrape OLX for a specific item across all keywords and categories."""
    all_deals: list[ScrapedDeal] = []
    seen_ids: set[str] = set()

    if search_paths is None:
        search_paths = _get_search_paths()
    for search_path in search_paths:
        for keyword in item.keywords:
            url = _build_search_url(keyword, 1, item.max_price, search_path)
            path_label = search_path or "geral"
            logger.info(f"OLX: searching '{keyword}' in {path_label} page 1")

            html = _fetch_page(url)
            if not html:
                continue

            total_pages = _get_total_pages(html)
            deals = _parse_listings(html)

            for deal in deals:
                if deal.external_id not in seen_ids and _matches_item(deal.title, item):
                    seen_ids.add(deal.external_id)
                    all_deals.append(deal)

            for page in range(2, total_pages + 1):
                await random_delay(RATE_LIMIT, RATE_LIMIT + 2)
                url = _build_search_url(keyword, page, item.max_price, search_path)
                logger.info(f"OLX: searching '{keyword}' in {path_label} page {page}")

                html = _fetch_page(url)
                if not html:
                    break

                deals = _parse_listings(html)
                if not deals:
                    break

                for deal in deals:
                    if deal.external_id not in seen_ids and _matches_item(deal.title, item):
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
