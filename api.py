"""FastAPI server — serves deal data to the poe-hub dashboard."""
import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from config_loader import config, settings
from models.database import Base, engine, get_db
from models.deals import Deal, PriceHistory, ManualPrice
from pipeline.runner import run_scrape

logging.basicConfig(level=logging.INFO)

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Hardware Deals API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# === Config ===

@app.get("/api/items")
def list_items():
    """Return configured search items with specs."""
    return [item.model_dump() for item in config.items]


# === Deals ===

@app.get("/api/deals")
def get_deals(
    item_name: str | None = None,
    source: str | None = None,
    category: str | None = None,
    max_price: float | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Get active deals with optional filters."""
    query = select(Deal).where(Deal.is_active == True)

    if item_name:
        query = query.where(Deal.item_name == item_name)
    if source:
        query = query.where(Deal.source == source)
    if category:
        query = query.where(Deal.category == category)
    if max_price:
        query = query.where(Deal.price <= max_price)

    query = query.order_by(Deal.price.asc()).offset(offset).limit(limit)
    deals = db.execute(query).scalars().all()

    return [
        {
            "id": d.id,
            "source": d.source,
            "item_name": d.item_name,
            "title": d.title,
            "price": d.price,
            "url": d.url,
            "location": d.location,
            "image_url": d.image_url,
            "category": d.category,
            "found_at": d.found_at.isoformat(),
        }
        for d in deals
    ]


@app.get("/api/deals/summary")
def deals_summary(db: Session = Depends(get_db)):
    """Price summary per item (min, avg, max, count) across all sources."""
    results = db.execute(
        select(
            Deal.item_name,
            Deal.source,
            func.min(Deal.price).label("min_price"),
            func.avg(Deal.price).label("avg_price"),
            func.max(Deal.price).label("max_price"),
            func.count(Deal.id).label("count"),
        )
        .where(Deal.is_active == True)
        .group_by(Deal.item_name, Deal.source)
    ).all()

    return [
        {
            "item_name": r.item_name,
            "source": r.source,
            "min_price": round(r.min_price, 2),
            "avg_price": round(float(r.avg_price), 2),
            "max_price": round(r.max_price, 2),
            "count": r.count,
        }
        for r in results
    ]


# === Price History ===

@app.get("/api/price-history/{item_name}")
def get_price_history(
    item_name: str,
    days: int = Query(30, le=365),
    db: Session = Depends(get_db),
):
    """Price history for charts."""
    since = datetime.utcnow() - timedelta(days=days)
    records = db.execute(
        select(PriceHistory)
        .where(PriceHistory.item_name == item_name, PriceHistory.recorded_at >= since)
        .order_by(PriceHistory.recorded_at.asc())
    ).scalars().all()

    return [
        {
            "source": r.source,
            "avg_price": r.avg_price,
            "min_price": r.min_price,
            "max_price": r.max_price,
            "deal_count": r.deal_count,
            "recorded_at": r.recorded_at.isoformat(),
        }
        for r in records
    ]


# === Manual Prices ===

@app.get("/api/manual-prices")
def get_manual_prices(db: Session = Depends(get_db)):
    records = db.execute(select(ManualPrice)).scalars().all()
    return [
        {
            "item_name": r.item_name,
            "price_new": r.price_new,
            "price_aliexpress": r.price_aliexpress,
            "price_reference": r.price_reference,
            "notes": r.notes,
        }
        for r in records
    ]


@app.post("/api/manual-prices")
def set_manual_price(
    item_name: str,
    price_new: float | None = None,
    price_aliexpress: float | None = None,
    price_reference: float | None = None,
    notes: str | None = None,
    db: Session = Depends(get_db),
):
    existing = db.execute(
        select(ManualPrice).where(ManualPrice.item_name == item_name)
    ).scalar_one_or_none()

    if existing:
        if price_new is not None:
            existing.price_new = price_new
        if price_aliexpress is not None:
            existing.price_aliexpress = price_aliexpress
        if price_reference is not None:
            existing.price_reference = price_reference
        if notes is not None:
            existing.notes = notes
    else:
        db.add(ManualPrice(
            item_name=item_name,
            price_new=price_new,
            price_aliexpress=price_aliexpress,
            price_reference=price_reference,
            notes=notes,
        ))
    db.commit()
    return {"status": "ok"}


# === Scrape trigger ===

@app.post("/api/scrape")
async def trigger_scrape():
    """Manually trigger a scrape cycle."""
    await run_scrape()
    return {"status": "completed"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host=settings.api_host, port=settings.api_port, reload=True)
