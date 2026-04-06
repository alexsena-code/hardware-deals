"""Proxy pool with health tracking — reads from DB, seeds from .env."""
import random
import logging
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from models.database import SessionLocal
from models.deals import Proxy
from config_loader import settings

logger = logging.getLogger(__name__)

MAX_FAILURES = 5


def seed_proxies():
    """Seed proxies from .env PROXY_LIST if DB is empty."""
    db = SessionLocal()
    try:
        count = db.execute(select(func.count(Proxy.id))).scalar()
        if count > 0:
            return

        proxy_list = [p.strip() for p in settings.proxy_list.split(",") if p.strip()]
        if not proxy_list:
            logger.warning("No proxies in .env and none in DB")
            return

        for url in proxy_list:
            db.add(Proxy(url=url))
        db.commit()
        logger.info(f"Seeded {len(proxy_list)} proxies from .env")
    finally:
        db.close()


class ProxyRotator:
    def __init__(self):
        self._cache: list[Proxy] = []
        self._last_refresh = None

    def _refresh(self, db: Session):
        """Reload active proxies from DB."""
        self._cache = db.execute(
            select(Proxy).where(Proxy.is_active == True, Proxy.fail_count < MAX_FAILURES)
        ).scalars().all()
        self._last_refresh = datetime.utcnow()

    def get_next(self) -> str | None:
        """Get a random healthy proxy."""
        try:
            db = SessionLocal()
            self._refresh(db)
            if not self._cache:
                all_proxies = db.execute(select(Proxy).where(Proxy.is_active == True)).scalars().all()
                if all_proxies:
                    for p in all_proxies:
                        p.fail_count = 0
                    db.commit()
                    self._refresh(db)

            if not self._cache:
                db.close()
                return None

            proxy = random.choice(self._cache)
            proxy.last_used = datetime.utcnow()
            db.commit()
            db.close()
            return proxy.url
        except Exception:
            return None  # No DB available (running as remote worker)

    def report_success(self, proxy_url: str):
        try:
            db = SessionLocal()
            proxy = db.execute(
                select(Proxy).where(Proxy.url == proxy_url)
            ).scalar_one_or_none()
            if proxy:
                proxy.fail_count = 0
                proxy.last_success = datetime.utcnow()
                proxy.last_error = None
                db.commit()
            db.close()
        except Exception:
            pass

    def report_failure(self, proxy_url: str, error: str = ""):
        try:
            db = SessionLocal()
            proxy = db.execute(
                select(Proxy).where(Proxy.url == proxy_url)
            ).scalar_one_or_none()
            if proxy:
                proxy.fail_count += 1
                proxy.last_error = error[:200] if error else None
                if proxy.fail_count >= MAX_FAILURES:
                    logger.warning(f"Proxy {proxy_url.split('@')[-1]} disabled ({MAX_FAILURES} failures)")
                db.commit()
            db.close()
        except Exception:
            pass


proxy_rotator = ProxyRotator()
