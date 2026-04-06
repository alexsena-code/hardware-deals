"""PCBuildWizard API — fetches new hardware prices from Brazilian stores."""
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.pcbuildwizard.com"

# Map hardware-deals categories → PCBuildWizard endpoints
CATEGORY_MAP = {
    "gpu": ("video-cards", 49),
    "cpu": ("cpus", 5),
    "cpu-kit": ("cpus", 5),       # No direct kit endpoint, use CPUs
    "ram": ("memory", 23),
    "psu": ("power-supplies", 37),
    "ssd": ("ssds", 43),
    "motherboard": ("motherboards", 24),
    "cooler": ("cpu-coolers", 2),
    "case": ("cases", 1),
    "monitor": ("monitors", 27),
}

DEFAULT_PARAMS = {
    "Channel": "pcbuildwizard-ws",
    "Country": "BR",
    "Manufacturers": "",
    "Merchants": "",
}


@dataclass
class NewPrice:
    """A product listing from PCBuildWizard."""
    name: str
    manufacturer: str
    cash_price: float
    installment_price: float | None
    merchant: str
    url: str | None
    category: str
    rating: float | None = None
    condition: str | None = None
    free_shipping: bool = False
    part_number: str | None = None


@dataclass
class PriceHistoryPoint:
    """A historical price data point."""
    product_name: str
    date: str
    price: float
    category: str


async def fetch_products(category: str, max_results: int = 500) -> list[NewPrice]:
    """Fetch current product listings for a category.

    Args:
        category: hardware-deals category (gpu, cpu, ram, etc.)
        max_results: max products to return (default 500 = effectively all)
    """
    mapping = CATEGORY_MAP.get(category)
    if not mapping:
        logger.warning("No PCBuildWizard mapping for category: %s", category)
        return []

    endpoint, _ = mapping
    url = f"{BASE_URL}/products/{endpoint}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, params=DEFAULT_PARAMS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("PCBuildWizard fetch failed for %s: %s", category, e)
        return []

    products = []
    items = data if isinstance(data, list) else data.get("products", data.get("items", []))

    for item in items[:max_results]:
        try:
            # Prefer finalPrice (after coupon), fallback to cashPrice
            cash_price = item.get("finalPrice") or item.get("cashPrice") or 0
            if not cash_price or cash_price <= 0:
                continue

            products.append(NewPrice(
                name=item.get("name") or item.get("shortDescription") or "Unknown",
                manufacturer=item.get("manufacturer") or "",
                cash_price=float(cash_price),
                installment_price=float(ip) if (ip := item.get("installmentPrice")) else None,
                merchant=item.get("merchantName") or item.get("merchantShortName") or "",
                url=item.get("merchantRedirectUrl"),
                category=category,
                rating=float(r) if (r := item.get("rating")) else None,
                condition=item.get("condition"),
                free_shipping=bool(item.get("freeShippingElegible")),
                part_number=item.get("partNumber"),
            ))
        except Exception as e:
            logger.debug("Failed to parse PCBuildWizard product: %s", e)

    logger.info("PCBuildWizard: %d products for %s", len(products), category)
    return products


async def fetch_price_history(
    category: str,
    months: int = 6,
    max_products: int = 20,
) -> list[PriceHistoryPoint]:
    """Fetch price history for a category.

    Args:
        category: hardware-deals category
        months: how many months of history
        max_products: max products to track
    """
    mapping = CATEGORY_MAP.get(category)
    if not mapping:
        return []

    _, category_id = mapping
    url = f"{BASE_URL}/products/price-history"
    params = {
        "Category": category_id,
        "ProductAggregation": 2,  # by model
        "MaxProducts": max_products,
        "Months": months,
        "TimeAggregation": 1,    # weekly
        "AggregationMethod": 2,  # median
        "PaymentMethod": "C",    # card (pix is usually cheaper)
        "Order": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("PCBuildWizard price history failed for %s: %s", category, e)
        return []

    points = []
    products = data if isinstance(data, list) else data.get("products", [])

    for product in products:
        product_name = product.get("productDescription") or product.get("name") or "Unknown"
        series = product.get("priceHistory") or []

        for point in series:
            try:
                price = point.get("finalPrice") or point.get("price")
                date = point.get("date")
                if price and date:
                    points.append(PriceHistoryPoint(
                        product_name=product_name,
                        date=str(date)[:10],  # keep just YYYY-MM-DD
                        price=float(price),
                        category=category,
                    ))
            except Exception:
                continue

    logger.info("PCBuildWizard: %d history points for %s", len(points), category)
    return points


async def find_best_new_price(category: str, keywords: list[str]) -> NewPrice | None:
    """Find the cheapest new product matching any keyword.

    Args:
        category: hardware-deals category
        keywords: keywords to match in product name
    """
    products = await fetch_products(category, max_results=100)
    if not products:
        return None

    matching = []
    for p in products:
        name_lower = p.name.lower()
        if any(kw.lower() in name_lower for kw in keywords):
            matching.append(p)

    if not matching:
        return None

    return min(matching, key=lambda p: p.cash_price)
