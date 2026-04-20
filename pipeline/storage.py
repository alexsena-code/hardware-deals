"""Save scraped deals to database and compute price history."""
import logging
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models.deals import Deal, PriceHistory, SearchItem
from sources.base import ScrapedDeal

logger = logging.getLogger(__name__)


def _check_alerts_for_deals(db: Session, deals: list[ScrapedDeal], item_name: str, category: str):
    """Send Discord alerts for new deals at least 40% below max price."""
    from pipeline.alerts import send_discord_alert, DISCORD_WEBHOOK_URL, ALERT_DISCOUNT_THRESHOLD
    if not DISCORD_WEBHOOK_URL:
        return
    item = db.execute(
        select(SearchItem).where(SearchItem.name == item_name)
    ).scalar_one_or_none()
    if not item:
        return
    for deal in deals:
        threshold = item.max_price * ALERT_DISCOUNT_THRESHOLD
        if deal.price > 0 and deal.price <= threshold:
            send_discord_alert(
                item_name=item_name,
                deal_title=deal.title,
                price=deal.price,
                max_price=item.max_price,
                url=deal.url,
                category=category,
            )


def upsert_deal(db: Session, deal: ScrapedDeal, item_name: str, category: str) -> bool:
    """Insert or update a deal. Returns True if new. Skips banned deals."""
    from models.deals import BannedDeal
    banned = db.execute(
        select(BannedDeal).where(BannedDeal.source == deal.source, BannedDeal.external_id == deal.external_id)
    ).scalar_one_or_none()
    if banned:
        return False
    stmt = pg_insert(Deal).values(
        source=deal.source,
        external_id=deal.external_id,
        item_name=item_name,
        title=deal.title,
        price=deal.price,
        url=deal.url,
        location=deal.location,
        image_url=deal.image_url,
        description=deal.description,
        category=category,
        is_active=True,
    ).on_conflict_do_update(
        index_elements=["source", "external_id"],
        set_={
            "price": deal.price,
            "title": deal.title,
            "is_active": True,
        },
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount > 0


def save_deals(db: Session, deals: list[ScrapedDeal], item_name: str, category: str) -> int:
    """Save multiple deals. Returns count of new/updated."""
    new_deals = []
    count = 0
    for deal in deals:
        if upsert_deal(db, deal, item_name, category):
            new_deals.append(deal)
            count += 1
    # Alert on new deals below max price
    if new_deals:
        try:
            _check_alerts_for_deals(db, new_deals, item_name, category)
        except Exception as e:
            logger.error("Alert check failed: %s", e)
    return count


def record_price_snapshot(db: Session, item_name: str, source: str):
    """Record price statistics for an item from a source."""
    result = db.execute(
        select(
            func.avg(Deal.price),
            func.min(Deal.price),
            func.max(Deal.price),
            func.count(Deal.id),
        ).where(
            Deal.item_name == item_name,
            Deal.source == source,
            Deal.is_active == True,
        )
    ).one()

    avg_price, min_price, max_price, count = result
    if count == 0:
        return

    snapshot = PriceHistory(
        item_name=item_name,
        source=source,
        avg_price=float(avg_price),
        min_price=float(min_price),
        max_price=float(max_price),
        deal_count=count,
    )
    db.add(snapshot)
    db.commit()
