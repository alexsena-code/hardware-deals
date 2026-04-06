"""Save scraped deals to database and compute price history."""
import logging
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models.deals import Deal, PriceHistory
from sources.base import ScrapedDeal

logger = logging.getLogger(__name__)


def upsert_deal(db: Session, deal: ScrapedDeal, item_name: str, category: str) -> bool:
    """Insert or update a deal. Returns True if new."""
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
    count = 0
    for deal in deals:
        if upsert_deal(db, deal, item_name, category):
            count += 1
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
