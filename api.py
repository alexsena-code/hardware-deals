"""FastAPI server — serves deal data to the poe-hub dashboard."""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config_loader import config, settings
from models.database import Base, engine, get_db, SessionLocal
from models.deals import Deal, PriceHistory, ManualPrice, SearchItem, Proxy
from pipeline.runner import run_scrape
from pipeline.proxy import seed_proxies

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Hardware Deals API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# === Seed items from config on startup ===

@app.on_event("startup")
def seed_items():
    """Seed search items from config.yaml if DB is empty."""
    from models.database import SessionLocal
    db = SessionLocal()
    try:
        count = db.execute(select(func.count(SearchItem.id))).scalar()
        if count == 0:
            logger.info("Seeding search items from config.yaml...")
            for item in config.items:
                db.add(SearchItem(
                    name=item.name,
                    keywords=item.keywords,
                    max_price=item.max_price,
                    category=item.category,
                    specs=item.specs.model_dump(exclude_none=True),
                ))
            db.commit()
            logger.info(f"Seeded {len(config.items)} items")
    finally:
        db.close()

    seed_proxies()


# === Items (from DB) ===

@app.get("/api/items")
def list_items(db: Session = Depends(get_db)):
    """Return search items from database."""
    items = db.execute(
        select(SearchItem).where(SearchItem.is_active == True)
    ).scalars().all()
    return [
        {
            "id": i.id,
            "name": i.name,
            "keywords": i.keywords,
            "max_price": i.max_price,
            "category": i.category,
            "specs": i.specs,
        }
        for i in items
    ]


class ItemCreate(BaseModel):
    name: str
    keywords: list[str]
    max_price: int
    category: str
    specs: dict = {}


@app.post("/api/items")
def create_item(body: ItemCreate, db: Session = Depends(get_db)):
    existing = db.execute(
        select(SearchItem).where(SearchItem.name == body.name)
    ).scalar_one_or_none()
    if existing:
        existing.keywords = body.keywords
        existing.max_price = body.max_price
        existing.category = body.category
        existing.specs = body.specs
        existing.is_active = True
    else:
        db.add(SearchItem(
            name=body.name,
            keywords=body.keywords,
            max_price=body.max_price,
            category=body.category,
            specs=body.specs,
        ))
    db.commit()
    return {"status": "ok"}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(SearchItem, item_id)
    if item:
        db.delete(item)
        db.commit()
    return {"status": "ok"}


# === Deals ===

class DealUpsert(BaseModel):
    source: str
    external_id: str
    item_name: str
    title: str
    price: float
    url: str
    location: str | None = None
    image_url: str | None = None
    description: str | None = None
    category: str


@app.post("/api/deals/upsert")
def upsert_deal(body: DealUpsert, db: Session = Depends(get_db)):
    """Upsert a deal from the remote scraper."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    stmt = pg_insert(Deal).values(
        source=body.source,
        external_id=body.external_id,
        item_name=body.item_name,
        title=body.title,
        price=body.price,
        url=body.url,
        location=body.location,
        image_url=body.image_url,
        description=body.description,
        category=body.category,
        is_active=True,
    ).on_conflict_do_update(
        index_elements=["source", "external_id"],
        set_={"price": body.price, "title": body.title, "is_active": True},
    )
    db.execute(stmt)
    db.commit()
    return {"status": "ok"}


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


@app.delete("/api/deals/all")
def clear_all_deals(db: Session = Depends(get_db)):
    """Clear all scraped deals."""
    count = db.execute(delete(Deal)).rowcount
    db.commit()
    return {"status": "ok", "deleted": count}


@app.delete("/api/deals/{deal_id}")
def delete_deal(deal_id: int, db: Session = Depends(get_db)):
    deal = db.get(Deal, deal_id)
    if deal:
        db.delete(deal)
        db.commit()
    return {"status": "ok"}


# === Price History ===

@app.get("/api/price-history/{item_name}")
def get_price_history(
    item_name: str,
    days: int = Query(30, le=365),
    db: Session = Depends(get_db),
):
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


@app.delete("/api/manual-prices/{item_name}")
def delete_manual_price(item_name: str, db: Session = Depends(get_db)):
    existing = db.execute(
        select(ManualPrice).where(ManualPrice.item_name == item_name)
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.commit()
    return {"status": "ok"}


# === Proxies ===

@app.get("/api/proxies")
def list_proxies(db: Session = Depends(get_db)):
    proxies = db.execute(select(Proxy)).scalars().all()
    return [
        {
            "id": p.id,
            "url": p.url,
            "is_active": p.is_active,
            "fail_count": p.fail_count,
            "last_used": p.last_used.isoformat() if p.last_used else None,
            "last_success": p.last_success.isoformat() if p.last_success else None,
            "last_error": p.last_error,
        }
        for p in proxies
    ]


@app.post("/api/proxies")
def add_proxy(url: str, db: Session = Depends(get_db)):
    existing = db.execute(select(Proxy).where(Proxy.url == url)).scalar_one_or_none()
    if existing:
        existing.is_active = True
        existing.fail_count = 0
    else:
        db.add(Proxy(url=url))
    db.commit()
    return {"status": "ok"}


@app.delete("/api/proxies/{proxy_id}")
def delete_proxy(proxy_id: int, db: Session = Depends(get_db)):
    proxy = db.get(Proxy, proxy_id)
    if proxy:
        db.delete(proxy)
        db.commit()
    return {"status": "ok"}


@app.post("/api/proxies/{proxy_id}/reset")
def reset_proxy(proxy_id: int, db: Session = Depends(get_db)):
    proxy = db.get(Proxy, proxy_id)
    if proxy:
        proxy.fail_count = 0
        proxy.is_active = True
        proxy.last_error = None
        db.commit()
    return {"status": "ok"}


@app.post("/api/proxies/test")
async def test_proxies(db: Session = Depends(get_db)):
    """Test all active proxies against OLX."""
    import httpx
    proxies = db.execute(select(Proxy).where(Proxy.is_active == True)).scalars().all()
    results = []
    for p in proxies:
        try:
            async with httpx.AsyncClient(proxies={"https://": p.url, "http://": p.url}, timeout=10) as client:
                resp = await client.get("https://www.olx.com.br")
                ok = resp.status_code == 200
                results.append({"id": p.id, "url": p.url.split("@")[-1], "ok": ok, "status": resp.status_code})
                if ok:
                    p.fail_count = 0
                    p.last_success = datetime.utcnow()
                else:
                    p.fail_count += 1
        except Exception as e:
            results.append({"id": p.id, "url": p.url.split("@")[-1], "ok": False, "error": str(e)[:100]})
            p.fail_count += 1
    db.commit()
    return results


# === WebSocket Scraper Worker ===

_worker_ws: WebSocket | None = None
_worker_connected = False


@app.websocket("/ws/scraper")
async def scraper_ws(ws: WebSocket):
    global _worker_ws, _worker_connected
    await ws.accept()
    _worker_ws = ws
    _worker_connected = True
    logger.info("Scraper worker connected")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "hello":
                logger.info("Worker hello: %s", msg.get("worker"))

            elif msg_type == "pong":
                pass

            elif msg_type == "deal":
                # Save deal to DB
                db = SessionLocal()
                try:
                    stmt = pg_insert(Deal).values(
                        source=msg.get("source", "olx"),
                        external_id=msg["external_id"],
                        item_name=msg["item_name"],
                        title=msg["title"],
                        price=msg["price"],
                        url=msg["url"],
                        location=msg.get("location"),
                        image_url=msg.get("image_url"),
                        description=msg.get("description"),
                        category=msg.get("category", "gpu"),
                        is_active=True,
                    ).on_conflict_do_update(
                        index_elements=["source", "external_id"],
                        set_={"price": msg["price"], "title": msg["title"], "is_active": True},
                    )
                    db.execute(stmt)
                    db.commit()
                finally:
                    db.close()

            elif msg_type == "status":
                logger.info("Worker status: %s — %s", msg.get("status"), msg.get("item", ""))

            elif msg_type == "result":
                logger.info(
                    "Scrape complete: %d deals in %ss",
                    msg.get("total_deals", 0), msg.get("duration_s", "?"),
                )

            elif msg_type == "error":
                logger.error("Worker error for %s: %s", msg.get("item"), msg.get("error"))

    except WebSocketDisconnect:
        logger.info("Scraper worker disconnected")
    finally:
        _worker_ws = None
        _worker_connected = False


@app.get("/api/worker/status")
def worker_status():
    return {"online": _worker_connected}


# === Scrape trigger ===

class ScrapeRequest(BaseModel):
    item_id: int | None = None


@app.post("/api/scrape")
async def trigger_scrape(body: ScrapeRequest | None = None, db: Session = Depends(get_db)):
    """Trigger scrape — all items or a single item by ID."""
    item_id = body.item_id if body else None

    if item_id:
        item = db.get(SearchItem, item_id)
        if not item:
            return {"status": "error", "message": "Item not found"}
        items_list = [item]
    else:
        items_list = db.execute(
            select(SearchItem).where(SearchItem.is_active == True)
        ).scalars().all()

    items_data = [
        {
            "name": i.name,
            "keywords": i.keywords,
            "max_price": i.max_price,
            "category": i.category,
            "specs": i.specs or {},
        }
        for i in items_list
    ]

    if _worker_connected and _worker_ws:
        task_id = str(uuid.uuid4())[:8]
        await _worker_ws.send_text(json.dumps({
            "type": "scrape",
            "id": task_id,
            "items": items_data,
        }))
        return {"status": "dispatched", "worker": "websocket", "task_id": task_id, "items": len(items_data)}
    else:
        await run_scrape()
        return {"status": "completed", "worker": "local"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host=settings.api_host, port=settings.api_port, reload=True)
