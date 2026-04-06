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
from models.deals import Deal, PriceHistory, ManualPrice, SearchItem, Proxy, OlxCategory
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
    seed_olx_categories()


def seed_olx_categories():
    """Seed OLX categories from config if DB is empty."""
    db = SessionLocal()
    try:
        count = db.execute(select(func.count(OlxCategory.id))).scalar()
        if count == 0:
            defaults = [
                ("/informatica", "Informatica"),
                ("/informatica/pecas-para-computador", "Pecas para Computador"),
                ("", "Busca Geral"),
            ]
            for path, label in defaults:
                db.add(OlxCategory(path=path, label=label))
            db.commit()
            logger.info(f"Seeded {len(defaults)} OLX categories")
    finally:
        db.close()


# === OLX Categories ===

@app.get("/api/olx-categories")
def list_olx_categories(db: Session = Depends(get_db)):
    cats = db.execute(select(OlxCategory)).scalars().all()
    return [
        {"id": c.id, "path": c.path, "label": c.label, "is_active": c.is_active}
        for c in cats
    ]


class CategoryCreate(BaseModel):
    path: str
    label: str


@app.post("/api/olx-categories")
def add_olx_category(body: CategoryCreate, db: Session = Depends(get_db)):
    existing = db.execute(
        select(OlxCategory).where(OlxCategory.path == body.path)
    ).scalar_one_or_none()
    if existing:
        existing.label = body.label
        existing.is_active = True
    else:
        db.add(OlxCategory(path=body.path, label=body.label))
    db.commit()
    return {"status": "ok"}


@app.delete("/api/olx-categories/{cat_id}")
def delete_olx_category(cat_id: int, db: Session = Depends(get_db)):
    cat = db.get(OlxCategory, cat_id)
    if cat:
        db.delete(cat)
        db.commit()
    return {"status": "ok"}


@app.patch("/api/olx-categories/{cat_id}/toggle")
def toggle_olx_category(cat_id: int, db: Session = Depends(get_db)):
    cat = db.get(OlxCategory, cat_id)
    if cat:
        cat.is_active = not cat.is_active
        db.commit()
        return {"status": "ok", "is_active": cat.is_active}
    return {"status": "error"}


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
            "scrape_enabled": i.scrape_enabled,
        }
        for i in items
    ]


class ItemCreate(BaseModel):
    name: str
    keywords: list[str]
    max_price: int
    category: str
    specs: dict = {}
    scrape_enabled: bool = True


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
        existing.scrape_enabled = body.scrape_enabled
        existing.is_active = True
    else:
        db.add(SearchItem(
            name=body.name,
            keywords=body.keywords,
            max_price=body.max_price,
            category=body.category,
            specs=body.specs,
            scrape_enabled=body.scrape_enabled,
        ))
    db.commit()
    return {"status": "ok"}


@app.put("/api/items/{item_id}")
def update_item(item_id: int, body: ItemCreate, db: Session = Depends(get_db)):
    item = db.get(SearchItem, item_id)
    if not item:
        return {"status": "error", "message": "Item not found"}
    item.name = body.name
    item.keywords = body.keywords
    item.max_price = body.max_price
    item.category = body.category
    item.specs = body.specs
    item.scrape_enabled = body.scrape_enabled
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
    limit: int = Query(200, le=2000),
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
            select(SearchItem).where(SearchItem.is_active == True, SearchItem.scrape_enabled == True)
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


# === PCBuildWizard — New Prices ===

@app.get("/api/new-prices/{category}")
async def get_new_prices(
    category: str,
    limit: int = Query(500, le=1000),
    search: str | None = None,
    # Spec filters (min values)
    min_vram: int | None = None,
    max_vram: int | None = None,
    min_capacity: int | None = None,
    max_capacity: int | None = None,
    min_wattage: int | None = None,
    min_refresh: int | None = None,
    min_size: float | None = None,
    max_size: float | None = None,
    socket: str | None = None,
    memory_type: str | None = None,
    form_factor: str | None = None,
    panel: str | None = None,
    resolution: str | None = None,
):
    """Fetch current new prices from PCBuildWizard for a category."""
    from sources.pcbuildwizard import fetch_products
    products = await fetch_products(category, max_results=limit)

    if search:
        q = search.lower()
        products = [p for p in products if q in p.name.lower() or q in p.manufacturer.lower()]

    # Spec-based filters
    def matches(p) -> bool:
        s = p.specs
        if min_vram and s.get("vram_gb", 0) < min_vram: return False
        if max_vram and s.get("vram_gb", 999) > max_vram: return False
        if min_capacity and s.get("capacity_gb", 0) < min_capacity: return False
        if max_capacity and s.get("capacity_gb", 99999) > max_capacity: return False
        if min_wattage and s.get("wattage", 0) < min_wattage: return False
        if min_refresh and s.get("refresh_rate", 0) < min_refresh: return False
        if min_size and s.get("size_inches", 0) < min_size: return False
        if max_size and s.get("size_inches", 999) > max_size: return False
        if socket and s.get("socket", "").replace(" ", "") != socket.replace(" ", ""): return False
        if memory_type and s.get("memory_type", "") != memory_type and s.get("type", "") != memory_type: return False
        if form_factor and s.get("form_factor", "") != form_factor: return False
        if panel and s.get("panel", "") != panel: return False
        if resolution and s.get("resolution", "") != resolution: return False
        return True

    if any([min_vram, max_vram, min_capacity, max_capacity, min_wattage,
            min_refresh, min_size, max_size, socket, memory_type, form_factor, panel, resolution]):
        products = [p for p in products if matches(p)]

    # Cache to DB
    _cache_products(products, category)

    return [
        {
            "name": p.name,
            "manufacturer": p.manufacturer,
            "cash_price": p.cash_price,
            "installment_price": p.installment_price,
            "merchant": p.merchant,
            "url": p.url,
            "category": p.category,
            "rating": p.rating,
            "free_shipping": p.free_shipping,
            "tag": p.tag,
            "details": p.details,
            "specs": p.specs,
        }
        for p in products
    ]


def _cache_products(products, category: str):
    """Cache products to DB for fallback and history."""
    from models.deals import StoreProduct, StoreProductHistory
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not products:
        return
    db = SessionLocal()
    try:
        for p in products:
            if not p.tag:
                continue
            # Upsert product
            stmt = pg_insert(StoreProduct).values(
                tag=p.tag,
                name=p.name,
                manufacturer=p.manufacturer,
                category=category,
                details=p.details,
                part_number=p.part_number,
                specs=p.specs,
                cash_price=p.cash_price,
                installment_price=p.installment_price,
                merchant=p.merchant,
                url=p.url,
                rating=p.rating,
                free_shipping=p.free_shipping,
                last_seen=datetime.utcnow(),
            ).on_conflict_do_update(
                index_elements=["tag"],
                set_={
                    "cash_price": p.cash_price,
                    "installment_price": p.installment_price,
                    "merchant": p.merchant,
                    "url": p.url,
                    "rating": p.rating,
                    "specs": p.specs,
                    "last_seen": datetime.utcnow(),
                },
            )
            db.execute(stmt)
        db.commit()
    except Exception as e:
        logger.warning("Failed to cache products: %s", e)
        db.rollback()
    finally:
        db.close()


@app.get("/api/new-prices-history/{category}")
async def get_new_price_history(
    category: str,
    months: int = Query(6, le=12),
    max_products: int = Query(10, le=50),
):
    """Fetch price history from PCBuildWizard for a category."""
    from sources.pcbuildwizard import fetch_price_history
    points = await fetch_price_history(category, months, max_products)
    return [
        {
            "product_name": p.product_name,
            "date": p.date,
            "price": p.price,
            "category": p.category,
        }
        for p in points
    ]


@app.post("/api/sync-new-prices")
async def sync_new_prices(db: Session = Depends(get_db)):
    """Sync best new prices from PCBuildWizard into ManualPrice table."""
    from sources.pcbuildwizard import find_best_new_price

    items = db.execute(
        select(SearchItem).where(SearchItem.is_active == True)
    ).scalars().all()

    updated = []
    for item in items:
        best = await find_best_new_price(item.category, item.keywords)
        if not best:
            continue

        existing = db.execute(
            select(ManualPrice).where(ManualPrice.item_name == item.name)
        ).scalar_one_or_none()

        if existing:
            existing.price_new = best.cash_price
            existing.notes = f"PCBuildWizard: {best.name} @ {best.merchant}"
        else:
            db.add(ManualPrice(
                item_name=item.name,
                price_new=best.cash_price,
                notes=f"PCBuildWizard: {best.name} @ {best.merchant}",
            ))

        updated.append({
            "item": item.name,
            "new_price": best.cash_price,
            "product": best.name,
            "merchant": best.merchant,
        })

    db.commit()
    return {"status": "ok", "updated": len(updated), "items": updated}


# === Analytics — Used vs New ===

@app.get("/api/analytics/price-comparison")
async def price_comparison(db: Session = Depends(get_db)):
    """Compare used (OLX) prices vs new (PCBuildWizard/manual) for each item."""
    # Get OLX deal stats per item
    olx_stats = db.execute(
        select(
            Deal.item_name,
            func.min(Deal.price).label("olx_min"),
            func.avg(Deal.price).label("olx_avg"),
            func.count(Deal.id).label("olx_count"),
        )
        .where(Deal.is_active == True, Deal.source == "olx")
        .group_by(Deal.item_name)
    ).all()

    olx_map = {
        r.item_name: {
            "olx_min": round(r.olx_min, 2),
            "olx_avg": round(float(r.olx_avg), 2),
            "olx_count": r.olx_count,
        }
        for r in olx_stats
    }

    # Get manual/new prices
    manual = db.execute(select(ManualPrice)).scalars().all()
    manual_map = {
        m.item_name: {
            "price_new": m.price_new,
            "price_aliexpress": m.price_aliexpress,
            "price_reference": m.price_reference,
            "notes": m.notes,
        }
        for m in manual
    }

    # Get items for max_price
    items = db.execute(select(SearchItem).where(SearchItem.is_active == True)).scalars().all()

    result = []
    for item in items:
        olx = olx_map.get(item.name, {})
        new = manual_map.get(item.name, {})
        olx_min = olx.get("olx_min")
        price_new = new.get("price_new")

        savings_pct = None
        if olx_min and price_new and price_new > 0:
            savings_pct = round((1 - olx_min / price_new) * 100, 1)

        result.append({
            "item_name": item.name,
            "category": item.category,
            "max_price": item.max_price,
            "olx_min": olx.get("olx_min"),
            "olx_avg": olx.get("olx_avg"),
            "olx_count": olx.get("olx_count", 0),
            "price_new": price_new,
            "price_aliexpress": new.get("price_aliexpress"),
            "savings_pct": savings_pct,
            "notes": new.get("notes"),
        })

    return sorted(result, key=lambda x: x.get("savings_pct") or 0, reverse=True)


@app.get("/api/analytics/price-trends")
def price_trends(
    days: int = Query(30, le=365),
    db: Session = Depends(get_db),
):
    """Get price history trends for all items (OLX snapshots)."""
    since = datetime.utcnow() - timedelta(days=days)
    records = db.execute(
        select(PriceHistory)
        .where(PriceHistory.recorded_at >= since)
        .order_by(PriceHistory.recorded_at.asc())
    ).scalars().all()

    # Group by item_name
    trends: dict = {}
    for r in records:
        if r.item_name not in trends:
            trends[r.item_name] = []
        trends[r.item_name].append({
            "date": r.recorded_at.isoformat()[:10],
            "avg_price": round(r.avg_price, 2),
            "min_price": round(r.min_price, 2),
            "deal_count": r.deal_count,
            "source": r.source,
        })

    return trends


# === Scheduler Status ===

_scheduler_running = False
_scheduler_jobs: list[dict] = []


@app.get("/api/scheduler/status")
def scheduler_status():
    return {
        "running": _scheduler_running,
        "jobs": _scheduler_jobs,
    }


@app.post("/api/scheduler/start")
async def start_scheduler():
    global _scheduler_running
    if _scheduler_running:
        return {"status": "already_running"}
    _start_background_scheduler()
    return {"status": "started"}


@app.post("/api/scheduler/stop")
async def stop_scheduler():
    global _scheduler_running, _bg_scheduler
    if _bg_scheduler:
        _bg_scheduler.shutdown(wait=False)
        _bg_scheduler = None
    _scheduler_running = False
    return {"status": "stopped"}


# === Background Scheduler (integrated into API) ===

_bg_scheduler = None


def _start_background_scheduler():
    """Start APScheduler as background jobs inside the API process."""
    global _bg_scheduler, _scheduler_running, _scheduler_jobs

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    if _bg_scheduler:
        return

    _bg_scheduler = AsyncIOScheduler()

    # Job 1: Price history snapshot every 6 hours
    async def snapshot_job():
        db = SessionLocal()
        try:
            items = db.execute(
                select(SearchItem).where(SearchItem.is_active == True)
            ).scalars().all()
            for item in items:
                record_price_snapshot_internal(db, item.name, "olx")
            logger.info("Price snapshot complete for %d items", len(items))
        finally:
            db.close()

    # Job 2: Sync new prices daily
    async def sync_job():
        from sources.pcbuildwizard import find_best_new_price
        db = SessionLocal()
        try:
            items = db.execute(
                select(SearchItem).where(SearchItem.is_active == True)
            ).scalars().all()
            count = 0
            for item in items:
                best = await find_best_new_price(item.category, item.keywords)
                if best:
                    existing = db.execute(
                        select(ManualPrice).where(ManualPrice.item_name == item.name)
                    ).scalar_one_or_none()
                    if existing:
                        existing.price_new = best.cash_price
                        existing.notes = f"PCBuildWizard: {best.name} @ {best.merchant}"
                    else:
                        db.add(ManualPrice(
                            item_name=item.name,
                            price_new=best.cash_price,
                            notes=f"PCBuildWizard: {best.name} @ {best.merchant}",
                        ))
                    count += 1
            db.commit()
            logger.info("Synced new prices for %d items", count)
        finally:
            db.close()

    _bg_scheduler.add_job(snapshot_job, "interval", hours=6, id="price_snapshot")
    _bg_scheduler.add_job(sync_job, "interval", hours=24, id="sync_new_prices")
    _bg_scheduler.start()

    _scheduler_running = True
    _scheduler_jobs = [
        {"id": "price_snapshot", "interval": "6h", "description": "OLX price history snapshot"},
        {"id": "sync_new_prices", "interval": "24h", "description": "PCBuildWizard new price sync"},
    ]
    logger.info("Background scheduler started with %d jobs", len(_scheduler_jobs))


def record_price_snapshot_internal(db, item_name: str, source: str):
    """Record price stats (inline version for scheduler)."""
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
    db.add(PriceHistory(
        item_name=item_name,
        source=source,
        avg_price=float(avg_price),
        min_price=float(min_price),
        max_price=float(max_price),
        deal_count=count,
    ))
    db.commit()


@app.on_event("startup")
async def auto_start_scheduler():
    """Auto-start scheduler on API boot."""
    _start_background_scheduler()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host=settings.api_host, port=settings.api_port, reload=True)
