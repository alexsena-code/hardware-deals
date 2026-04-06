"""Match scraped deals to configured items."""
import logging
from config_loader import SearchItem, config
from sources.base import ScrapedDeal

logger = logging.getLogger(__name__)


def match_deal_to_item(deal: ScrapedDeal) -> SearchItem | None:
    """Find which configured item a deal matches."""
    title_lower = deal.title.lower()
    for item in config.items:
        for keyword in item.keywords:
            if keyword.lower() in title_lower:
                return item
    return None


def is_good_deal(deal: ScrapedDeal, item: SearchItem) -> bool:
    """Check if deal price is within max_price threshold."""
    return deal.price <= item.max_price
