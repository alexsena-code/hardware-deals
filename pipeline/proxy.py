import random
import logging
from config_loader import settings

logger = logging.getLogger(__name__)


class ProxyRotator:
    def __init__(self):
        self._proxies = [p.strip() for p in settings.proxy_list.split(",") if p.strip()]
        self._failed: dict[str, int] = {}  # proxy -> consecutive failures
        self._max_failures = 3

        if self._proxies:
            logger.info(f"Loaded {len(self._proxies)} proxies")
        else:
            logger.warning("No proxies configured — running without proxy")

    def get_next(self) -> str | None:
        if not self._proxies:
            return None

        # Filter out proxies with too many consecutive failures
        available = [p for p in self._proxies if self._failed.get(p, 0) < self._max_failures]

        if not available:
            # Reset all failures and try again
            logger.warning("All proxies failed — resetting failure counts")
            self._failed.clear()
            available = self._proxies

        return random.choice(available)

    def report_success(self, proxy: str):
        """Reset failure count on success."""
        if proxy in self._failed:
            del self._failed[proxy]

    def report_failure(self, proxy: str):
        """Track consecutive failures."""
        self._failed[proxy] = self._failed.get(proxy, 0) + 1
        if self._failed[proxy] >= self._max_failures:
            logger.warning(f"Proxy {proxy.split('@')[-1]} temporarily disabled ({self._max_failures} failures)")

    @property
    def available_count(self) -> int:
        if not self._proxies:
            return 0
        return len([p for p in self._proxies if self._failed.get(p, 0) < self._max_failures])


proxy_rotator = ProxyRotator()
