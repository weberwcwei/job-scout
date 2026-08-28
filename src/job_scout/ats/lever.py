"""Lever adapter: public postings API.

Endpoint: https://api.lever.co/v0/postings/<slug>?mode=json
Each item: {"id", "text", "hostedUrl", "applyUrl", "categories" (location,
team, commitment), "lists" ...}. "commitment" category holds employment type.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from job_scout.ats.base import ATSAdapter, normalize_employment_type, parse_ats_location
from job_scout.models import ATSJob, ATSProvider, Site

log = logging.getLogger("job_scout.ats.lever")

LEVER_API = "https://api.lever.co/v0/postings/{slug}"


class LeverAdapter(ATSAdapter):
    site = Site.LEVER
    provider = ATSProvider.LEVER

    def fetch_jobs(self, client: httpx.Client, slug: str) -> list[ATSJob]:
        url = f"{LEVER_API.format(slug=slug)}?mode=json"
        payload = self._get_json(client, url)
        if not isinstance(payload, list):
            return []
        jobs: list[ATSJob] = []
        for item in payload:
            job = self._parse_item(item)
            if job is not None:
                jobs.append(job)
        return jobs

    def _parse_item(self, item: object) -> ATSJob | None:
        if not isinstance(item, dict):
            return None
        source_id = str(item.get("id") or "")
        if not source_id:
            return None
        title = str(item.get("text") or "Untitled")

        categories = item.get("categories") or {}
        if not isinstance(categories, dict):
            categories = {}
        location_text = categories.get("location") or ""
        commitment = categories.get("commitment") or ""

        location = parse_ats_location(
            location_text if isinstance(location_text, str) else None
        )

        posted_at = _parse_epoch(item.get("createdAt"))
        description_plain = str(item.get("descriptionPlain") or "")

        return ATSJob(
            source=self.site,
            source_id=source_id,
            url=str(item.get("hostedUrl") or ""),
            apply_url=str(item.get("applyUrl") or ""),
            title=title,
            location=location,
            location_text=str(location_text) if location_text else None,
            employment_type=normalize_employment_type(commitment),
            description=description_plain,
            posted_at=posted_at,
            updated_at=posted_at or datetime.now(),
            last_seen_at=posted_at or datetime.now(),
            first_seen_at=datetime.now(),
        )


def _parse_epoch(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000)
        except (OverflowError, OSError, ValueError):
            return None
    return None
