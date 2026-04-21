"""Discord webhook alerts for deals below max price."""
import logging
import os
import requests

from config_loader import settings

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL") or settings.discord_webhook_url or ""

ALERT_DISCOUNT_THRESHOLD = 0.5  # Alert if price <= max_price * 0.5 (i.e. 50% discount)


def send_discord_alert(item_name: str, deal_title: str, price: float, max_price: float, url: str, category: str):
    """Send a Discord embed when a deal is found below the item's max price."""
    if not DISCORD_WEBHOOK_URL:
        return

    savings = max_price - price
    savings_pct = round((savings / max_price) * 100) if max_price > 0 else 0

    # Color based on savings %
    if savings_pct >= 30:
        color = 0x00FF00  # green — great deal
    elif savings_pct >= 15:
        color = 0xFFFF00  # yellow — good deal
    else:
        color = 0xFF8C00  # orange — okay deal

    price_fmt = f"R$ {price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    max_fmt = f"R$ {max_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    savings_fmt = f"R$ {savings:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    embed = {
        "title": f"🔥 {item_name} — {price_fmt}",
        "description": deal_title[:256],
        "url": url,
        "color": color,
        "fields": [
            {"name": "Preço", "value": price_fmt, "inline": True},
            {"name": "Max Config.", "value": max_fmt, "inline": True},
            {"name": "Economia", "value": f"{savings_fmt} ({savings_pct}%)", "inline": True},
            {"name": "Categoria", "value": category.upper(), "inline": True},
        ],
    }

    payload = {
        "username": "Hardware Deals",
        "embeds": [embed],
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text[:100])
    except Exception as e:
        logger.error("Discord alert failed: %s", e)


def check_and_alert(deals_list: list[dict], items_cache: dict):
    """Check a batch of deals and send alerts for those at least 50% below max price.
    items_cache: dict of item_name -> SearchItem (DB model with max_price).
    """
    if not DISCORD_WEBHOOK_URL:
        logger.warning("Discord webhook not configured — skipping %d deal(s)", len(deals_list))
        return

    sent = 0
    for deal in deals_list:
        item_name = deal.get("item_name", "")
        price = deal.get("price", 0)
        item = items_cache.get(item_name)
        if not item:
            continue

        threshold = item.max_price * ALERT_DISCOUNT_THRESHOLD
        if price > 0 and price <= threshold:
            send_discord_alert(
                item_name=item_name,
                deal_title=deal.get("title", ""),
                price=price,
                max_price=item.max_price,
                url=deal.get("url", ""),
                category=deal.get("category", ""),
            )
            sent += 1

    if deals_list:
        logger.info("Discord alerts: %d sent / %d new deals", sent, len(deals_list))
