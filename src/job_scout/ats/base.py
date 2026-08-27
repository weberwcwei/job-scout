"""ATS adapter base class for direct board polling.

Adapters fetch a board's public JSON, normalise each posting into the shared
:class:`~job_scout.models.ATSJob` model, and return the list. The caller
(registry/poller) tags each job with the resolved company identity.

See ATS_DISCOVERY.md for the full design.
"""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod

import httpx

from job_scout.config import ScrapingConfig
from job_scout.models import ATSJob, ATSProvider, Company, Location, Site

log = logging.getLogger("job_scout.ats")

#: Commitment strings ATSs surface, normalised to the canonical job schema.
_EMPLOYMENT_MAP = {
    "full-time": "full_time",
    "full time": "full_time",
    "permanent": "full_time",
    "part-time": "part_time",
    "part time": "part_time",
    "contract": "contract",
    "temporary": "temporary",
    "casual": "temporary",
    "internship": "internship",
    "intern": "internship",
}


def normalize_employment_type(text: str | None) -> str | None:
    if not text:
        return None
    key = text.strip().lower()
    if key in _EMPLOYMENT_MAP:
        return _EMPLOYMENT_MAP[key]
    for prefix, value in _EMPLOYMENT_MAP.items():
        if key.startswith(prefix):
            return value
    return None


def parse_ats_location(text: str | None, *, default_country: str = "AU") -> Location:
    """Parse an ATS free-text location into a Location.

    Relies on the Location model validator to normalise country names/codes.
    Remote postings are detected from the text; "hybrid" is left to the caller
    because it is often a workplace-type field, not part of the location.
    """
    if not text or not text.strip():
        return Location(country=default_country)
    raw = text.strip()
    if "remote" in raw.lower():
        return Location(is_remote=True, country=default_country)
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) == 1:
        return Location(city=parts[0], country=default_country)
    if len(parts) == 2:
        return Location(city=parts[0], state=parts[1], country=default_country)
    return Location(city=parts[0], state=parts[1], country=parts[2])


class ATSAdapter(ABC):
    """Fetches normalised ATSJob records from a single ATS board."""

    site: Site
    provider: ATSProvider

    def __init__(self, config: ScrapingConfig):
        self.config = config

    @abstractmethod
    def fetch_jobs(self, client: httpx.Client, slug: str) -> list[ATSJob]:
        """Fetch and normalise all jobs for a board slug."""

    def poll(self, company: Company) -> list[ATSJob]:
        """Poll a company's board and tag each job with its company identity."""
        slug = company.ats_slug or ""
        if not slug:
            log.warning("company %r has no ats_slug; skipping poll", company.name)
            return []
        with self._make_client() as client:
            jobs = self.fetch_jobs(client, slug)
        for job in jobs:
            job.company_id = company.id
            job.company = company.name
        return jobs

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.request_timeout,
            follow_redirects=True,
            headers={"accept": "application/json", "user-agent": "job-scout/0.1"},
        )

    def _get_json(self, client: httpx.Client, url: str) -> object | None:
        for attempt in range(self.config.max_retries + 1):
            self._delay()
            try:
                resp = client.get(url)
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait = (
                        float(retry_after)
                        if retry_after and retry_after.isdigit()
                        else 2**attempt * 5
                    )
                    log.warning("%s: 429 from %s, backing off %ss", self.provider.value, url, wait)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500 and attempt < self.config.max_retries:
                    time.sleep(2**attempt * 3)
                    continue
                if resp.status_code != 200:
                    log.warning(
                        "%s: HTTP %s from %s", self.provider.value, resp.status_code, url
                    )
                    return None
                return resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.error("%s: %s", self.provider.value, e)
                if attempt < self.config.max_retries:
                    time.sleep(2**attempt * 3)
                    continue
                return None
        return None

    def _delay(self) -> None:
        time.sleep(
            random.uniform(self.config.delay_min_seconds, self.config.delay_max_seconds)
        )