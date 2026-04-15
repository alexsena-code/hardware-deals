"""
Hardware Scrape WebSocket Worker — runs on local PC, connects to VPS API.

Receives scrape commands via WebSocket, executes OLX scraping (residential IP),
and sends results back to VPS for storage.

Usage:
  python scrape_worker.py
  python scrape_worker.py --ws-url wss://api.pathoftrade.net/hardware-api/ws/scraper

Environment:
  WS_URL — WebSocket URL (default: wss://api.pathoftrade.net/hardware-api/ws/scraper)
"""
import argparse
import asyncio
import json
import logging
import platform
import signal
import sys
import time

try:
    import websockets
except ImportError:
    print("Missing dependency: pip install websockets")
    sys.exit(1)

from config_loader import SearchItem
from sources.olx import scrape_olx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scrape-worker")

BACKOFF_BASE = 5
BACKOFF_MAX = 60
shutdown_event = asyncio.Event()


def _setup_signals():
    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown_event.set)
    else:
        signal.signal(signal.SIGINT, lambda *_: shutdown_event.set())


CONCURRENCY = 3  # parallel scrape tasks (sequential within thread to avoid blocking event loop)


async def _scrape_item(ws, ws_lock, sem, task_id, item_data, search_paths, olx_categories=None):
    """Scrape a single item with concurrency limit."""
    async with sem:
        item = SearchItem(
            name=item_data["name"],
            keywords=item_data["keywords"],
            max_price=item_data["max_price"],
            category=item_data["category"],
            specs=item_data.get("specs", {}),
        )

        # Filter search paths by item category if olx_categories data is available
        item_search_paths = search_paths
        if olx_categories:
            item_search_paths = [
                c["path"] for c in olx_categories
                if not c.get("allowed_item_categories") or item.category in c["allowed_item_categories"]
            ] or search_paths

        async with ws_lock:
            await ws.send(json.dumps({
                "type": "status", "id": task_id,
                "status": "scraping", "item": item.name,
            }))

        count = 0
        try:
            deals = await scrape_olx(item, search_paths=item_search_paths)
            log.info("%s: %d deals found", item.name, len(deals))

            # Send deals in batches to avoid WebSocket timeout
            BATCH_SIZE = 50
            deals_data = [
                {
                    "source": deal.source,
                    "external_id": deal.external_id,
                    "item_name": item.name,
                    "title": deal.title,
                    "price": deal.price,
                    "url": deal.url,
                    "location": deal.location,
                    "image_url": deal.image_url,
                    "image_urls": deal.image_urls or [],
                    "description": deal.description,
                    "category": item.category,
                }
                for deal in deals
            ]
            for i in range(0, len(deals_data), BATCH_SIZE):
                batch = deals_data[i:i + BATCH_SIZE]
                async with ws_lock:
                    await ws.send(json.dumps({
                        "type": "deals_batch",
                        "id": task_id,
                        "deals": batch,
                    }))
                count += len(batch)
        except Exception as e:
            log.error("Scrape failed for %s: %s", item.name, e)
            async with ws_lock:
                await ws.send(json.dumps({
                    "type": "error", "id": task_id,
                    "item": item.name, "error": str(e),
                }))
        return count


async def _execute_scrape(ws, msg):
    """Execute a scrape task with parallel workers."""
    task_id = msg.get("id", "unknown")
    items_data = msg.get("items", [])
    search_paths = msg.get("search_paths")
    olx_categories = msg.get("olx_categories")

    if not items_data:
        log.warning("No items to scrape")
        await ws.send(json.dumps({
            "type": "result", "id": task_id,
            "status": "complete", "total_deals": 0,
        }))
        return

    log.info("Task %s: scraping %d items with %d parallel workers", task_id, len(items_data), CONCURRENCY)
    t0 = time.monotonic()

    sem = asyncio.Semaphore(CONCURRENCY)
    ws_lock = asyncio.Lock()

    results = await asyncio.gather(*[
        _scrape_item(ws, ws_lock, sem, task_id, item_data, search_paths, olx_categories)
        for item_data in items_data
    ], return_exceptions=True)
    total_deals = sum(r for r in results if isinstance(r, int))

    duration = round(time.monotonic() - t0, 1)
    log.info("Task %s done: %d deals in %.1fs", task_id, total_deals, duration)
    await ws.send(json.dumps({
        "type": "result", "id": task_id,
        "status": "complete",
        "total_deals": total_deals,
        "duration_s": duration,
    }))


async def _worker(ws_url: str):
    """Main worker loop with auto-reconnect."""
    backoff = BACKOFF_BASE

    while not shutdown_event.is_set():
        try:
            log.info("Connecting to %s", ws_url)
            async with websockets.connect(ws_url, ping_interval=30, ping_timeout=120) as ws:
                backoff = BACKOFF_BASE
                log.info("Connected. Sending hello.")
                await ws.send(json.dumps({
                    "type": "hello",
                    "worker": "hardware-scraper",
                    "platform": platform.platform(),
                }))

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msg_type = msg.get("type")
                    if msg_type == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                    elif msg_type == "scrape":
                        await _execute_scrape(ws, msg)
                    else:
                        log.debug("Unknown message type: %s", msg_type)

        except (websockets.ConnectionClosed, OSError) as e:
            if shutdown_event.is_set():
                break
            log.warning("Connection lost (%s). Reconnecting in %ds...", e, backoff)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, BACKOFF_MAX)
        except Exception:
            if shutdown_event.is_set():
                break
            log.exception("Unexpected error. Reconnecting in %ds...", backoff)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, BACKOFF_MAX)


def main():
    parser = argparse.ArgumentParser(description="Hardware scrape WebSocket worker")
    parser.add_argument(
        "--ws-url", type=str,
        default="wss://api.pathoftrade.net/hardware-api/ws/scraper",
        help="WebSocket URL",
    )
    args = parser.parse_args()

    _setup_signals()
    try:
        asyncio.run(_worker(args.ws_url))
    except KeyboardInterrupt:
        pass
    log.info("Worker stopped.")


if __name__ == "__main__":
    main()
