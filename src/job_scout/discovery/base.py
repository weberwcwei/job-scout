"""Discovery source base: shared HTTP helpers and the source contract.

Each discovery source produces a list of partial :class:`~job_scout.models.Company`
records. The orchestrator (``discovery.__init__.run_discovery``) merges them
into the canonical registry and de-duplicates by name.

Discovery is best-effort and rate-limit aware: a source that fails returns an
empty list and logs, never raises.
"""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod

import httpx
from bs4 import BeautifulSoup

from job_scout.config import DiscoveryConfig
from job_scout.models import Company

log = logging.getLogger("job_scout.discovery")

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class DiscoverySource(ABC):
    """A source of candidate companies."""

    name: str = "source"

    def __init__(self, config: DiscoveryConfig):
        self.config = config

    @abstractmethod
    def discover(self) -> list[Company]:
        """Return candidate companies. Never raises — return [] on failure."""

    def _client(self, timeout: float = 15.0) -> httpx.Client:
        return httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"user-agent": _UA, "accept-language": "en-AU,en;q=0.9"},
        )

    def _get_html(self, client: httpx.Client, url: str) -> BeautifulSoup | None:
        try:
            resp = client.get(url)
        except httpx.HTTPError as e:
            log.warning("%s: GET %s failed: %s", self.name, url, e)
            return None
        if resp.status_code != 200:
            log.warning("%s: HTTP %s from %s", self.name, resp.status_code, url)
            return None
        return BeautifulSoup(resp.text, "html.parser")

    def _get_text(self, client: httpx.Client, url: str) -> str | None:
        try:
            resp = client.get(url)
        except httpx.HTTPError as e:
            log.warning("%s: GET %s failed: %s", self.name, url, e)
            return None
        if resp.status_code != 200:
            log.warning("%s: HTTP %s from %s", self.name, resp.status_code, url)
            return None
        return resp.text

    @staticmethod
    def _delay(min_s: float = 0.2, max_s: float = 0.8) -> None:
        time.sleep(random.uniform(min_s, max_s))