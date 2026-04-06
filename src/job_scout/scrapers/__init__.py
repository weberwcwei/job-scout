"""Scraper base class and registry."""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod

import httpx

from job_scout.config import ScrapingConfig
from job_scout.models import Job, ScrapeParams, Site

log = logging.getLogger("job_scout.scrapers")


class BaseScraper(ABC):
    site: Site

    def __init__(self, config: ScrapingConfig):
        self.config = config
        self._seen_ids: set[str] = set()
        self._proxy_index: int = 0

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
        for attempt in range(self.config.max_retries + 1):
            self._delay()
            try:
                resp = client.get(url, **kwargs)
                if resp.status_code == 429:
                    wait = min(2**attempt * 10, 60)
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
        for attempt in range(self.config.max_retries + 1):
            self._delay()
            try:
                resp = client.post(url, **kwargs)
                if resp.status_code == 429:
                    wait = min(2**attempt * 10, 60)
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


def get_scraper(site: str, config: ScrapingConfig) -> BaseScraper:
    from job_scout.scrapers.bayt import BaytScraper
    from job_scout.scrapers.glassdoor import GlassdoorScraper
    from job_scout.scrapers.google import GoogleScraper
    from job_scout.scrapers.indeed import IndeedScraper
    from job_scout.scrapers.linkedin import LinkedInScraper
    from job_scout.scrapers.ziprecruiter import ZipRecruiterScraper

    registry = {
        "linkedin": LinkedInScraper,
        "indeed": IndeedScraper,
        "google": GoogleScraper,
        "glassdoor": GlassdoorScraper,
        "ziprecruiter": ZipRecruiterScraper,
        "bayt": BaytScraper,
    }
    cls = registry.get(site)
    if not cls:
        raise ValueError(f"Unknown site: {site}")
    return cls(config)
