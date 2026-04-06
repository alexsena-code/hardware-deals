import itertools
from config_loader import settings


class ProxyRotator:
    def __init__(self):
        proxies = [p.strip() for p in settings.proxy_list.split(",") if p.strip()]
        if not proxies:
            self._cycle = None
        else:
            self._cycle = itertools.cycle(proxies)

    def get_next(self) -> str | None:
        if self._cycle is None:
            return None
        return next(self._cycle)

    def get_httpx_proxy(self) -> dict | None:
        proxy = self.get_next()
        if proxy is None:
            return None
        return {"http://": proxy, "https://": proxy}


proxy_rotator = ProxyRotator()
