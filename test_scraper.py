"""Test scrapers without database — just print results."""
import asyncio
import logging
import sys

from config_loader import config
from sources.olx import scrape_olx
from sources.ebay import scrape_ebay

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


async def test_item(item_name: str | None = None, source: str = "olx"):
    items = config.items
    if item_name:
        items = [i for i in items if item_name.lower() in i.name.lower()]
        if not items:
            print(f"Item '{item_name}' not found. Available:")
            for i in config.items:
                print(f"  - {i.name}")
            return

    for item in items:
        print(f"\n{'='*60}")
        print(f"  {item.name} | max R$ {item.max_price} | source: {source}")
        print(f"{'='*60}")

        if source == "olx":
            deals = await scrape_olx(item)
        elif source == "ebay":
            deals = await scrape_ebay(item)
        else:
            print(f"Source '{source}' unknown")
            return

        if not deals:
            print("  No deals found.")
            continue

        deals.sort(key=lambda d: d.price)
        for d in deals:
            flag = " <<<" if d.price <= item.max_price else ""
            print(f"  R$ {d.price:>8,.0f} | {d.title[:55]:<55} | {d.location or 'N/A'}{flag}")

        print(f"\n  Total: {len(deals)} | Min: R$ {min(d.price for d in deals):,.0f} | Max: R$ {max(d.price for d in deals):,.0f}")
        good = [d for d in deals if d.price <= item.max_price]
        if good:
            print(f"  Within budget: {len(good)} deals")

        # Only test first item if testing all
        if not item_name:
            print("\n  (testing only first item — pass item name to test specific)")
            break


if __name__ == "__main__":
    item = sys.argv[1] if len(sys.argv) > 1 else None
    source = sys.argv[2] if len(sys.argv) > 2 else "olx"
    asyncio.run(test_item(item, source))
