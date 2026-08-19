"""Scraper base class and registry."""

from __future__ import annotations

import logging
import random
import threading
import time
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx

from job_scout.config import ScrapingConfig
from job_scout.models import Job, ScrapeParams, Site

log = logging.getLogger("job_scout.scrapers")

# Process-wide per-host request gate: workers in different tasks each build
# their own client, so without shared state the aggregate request rate to a
# host is unbounded (6 workers sleeping 0.5-2s in parallel still burst).
_host_lock = threading.Lock()
_last_request_at: dict[str, float] = {}


class DescriptionCache:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}
        self._inflight: set[tuple[str, str]] = set()
        self._condition = threading.Condition()

    def get_or_fetch(self, key: tuple[str, str], fetch) -> str:
        with self._condition:
            while key in self._inflight:
                self._condition.wait()
            if key in self._values:
                return self._values[key]
            self._inflight.add(key)

        try:
            value = fetch()
        except BaseException:
            with self._condition:
                self._inflight.remove(key)
                self._condition.notify_all()
            raise

        with self._condition:
            self._inflight.remove(key)
            if value is not None:
                self._values[key] = value
            self._condition.notify_all()
        return value or ""

    def __getitem__(self, key: tuple[str, str]) -> str:
        with self._condition:
            return self._values[key]


def _wait_host_gate(host: str, min_interval: float) -> None:
    """Sleep until `min_interval` seconds have passed since the last request
    to `host` from any worker in this process."""
    if min_interval <= 0 or not host:
        return
    while True:
        with _host_lock:
            now = time.monotonic()
            wait = _last_request_at.get(host, 0.0) + min_interval - now
            if wait <= 0:
                _last_request_at[host] = now
                return
        time.sleep(wait)


class ScraperError(Exception):
    """Base exception for scraper failures."""

    category: str = "unknown"


class NetworkError(ScraperError):
    category = "network"


class AuthError(ScraperError):
    category = "auth"


class ParseError(ScraperError):
    category = "parse"


class RateLimitError(ScraperError):
    category = "rate_limit"


class BaseScraper(ABC):
    site: Site

    def __init__(self, config: ScrapingConfig):
        self.config = config
        self._seen_ids: set[str] = set()
        self._proxy_index: int = 0
        # Shared across scraper instances for one scrape run: (site, job_id) -> description.
        # Lets multiple search terms that surface the same job fetch its description once.
        self.description_cache: DescriptionCache | None = None

    @abstractmethod
    def scrape(self, params: ScrapeParams) -> list[Job]: ...

    def _next_proxy(self) -> str | None:
        if not self.config.proxies:
            return None
        proxy = self.config.proxies[self._proxy_index % len(self.config.proxies)]
        self._proxy_index += 1
        return proxy

    def _make_client(self):
        proxy = self._next_proxy()
        if self.config.use_tls_fingerprinting:
            try:
                from job_scout.scrapers.tls import create_tls_client

                return create_tls_client(
                    proxy=proxy, timeout=self.config.request_timeout
                )
            except ImportError:
                log.warning("curl_cffi not installed, falling back to httpx")
        kwargs: dict = {
            "timeout": self.config.request_timeout,
            "follow_redirects": True,
        }
        if proxy:
            kwargs["proxy"] = proxy
        return httpx.Client(**kwargs)

    def _get_with_retry(
        self, client: httpx.Client, url: str, **kwargs
    ) -> httpx.Response | None:
        host = urlparse(url).netloc
        for attempt in range(self.config.max_retries + 1):
            self._delay()
            _wait_host_gate(host, self.config.min_request_interval_seconds)
            try:
                resp = client.get(url, **kwargs)
                if resp.status_code == 429:
                    wait = min(2**attempt * 10, 60)
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait = max(wait, int(retry_after))
                    log.warning(f"429 from {self.site.value}, backing off {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500 and attempt < self.config.max_retries:
                    time.sleep(2**attempt * 5)
                    continue
                return resp
            except httpx.HTTPError as e:
                log.error(f"{self.site.value}: {e}")
                if attempt < self.config.max_retries:
                    time.sleep(2**attempt * 3)
                    continue
                return None
        return None

    def _post_with_retry(
        self, client: httpx.Client, url: str, **kwargs
    ) -> httpx.Response | None:
        host = urlparse(url).netloc
        for attempt in range(self.config.max_retries + 1):
            self._delay()
            _wait_host_gate(host, self.config.min_request_interval_seconds)
            try:
                resp = client.post(url, **kwargs)
                if resp.status_code == 429:
                    wait = min(2**attempt * 10, 60)
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait = max(wait, int(retry_after))
                    log.warning(f"429 from {self.site.value}, backing off {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500 and attempt < self.config.max_retries:
                    time.sleep(2**attempt * 5)
                    continue
                return resp
            except httpx.HTTPError as e:
                log.error(f"{self.site.value}: {e}")
                if attempt < self.config.max_retries:
                    time.sleep(2**attempt * 3)
                    continue
                return None
        return None

    def _delay(self) -> None:
        time.sleep(
            random.uniform(self.config.delay_min_seconds, self.config.delay_max_seconds)
        )

    def _is_dup(self, source_id: str) -> bool:
        if source_id in self._seen_ids:
            return True
        self._seen_ids.add(source_id)
        return False

    def _debug_response(self, resp: httpx.Response | None, context: str = "") -> None:
        """Log response snippet when debug mode is on — call on parse failures."""
        if not self.config.debug or resp is None:
            return
        snippet = resp.text[:500] if resp.text else "(empty body)"
        log.debug(
            "%s debug — %s: status=%s, body=%.500s",
            self.site.value,
            context,
            resp.status_code,
            snippet,
        )


def _scraper_registry():
    from job_scout.scrapers.indeed import IndeedScraper
    from job_scout.scrapers.jora import JoraScraper
    from job_scout.scrapers.linkedin import LinkedInScraper

    return {
        "linkedin": LinkedInScraper,
        "indeed": IndeedScraper,
        "jora": JoraScraper,
    }


def get_supported_sites() -> frozenset[str]:
    return frozenset(_scraper_registry())


def get_scraper(site: str, config: ScrapingConfig) -> BaseScraper:
    registry = _scraper_registry()
    cls = registry.get(site)
    if not cls:
        raise ValueError(f"Unknown site: {site}")
    return cls(config)
