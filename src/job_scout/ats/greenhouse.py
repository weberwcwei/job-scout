"""Greenhouse adapter: public board API.

Endpoint: https://boards-api.greenhouse.io/v1/boards/<slug>/jobs
Each item: {"id", "title", "absolute_url", "location", "metadata"
(via the ?questions=true query param), "updated_at"}. The plain endpoint
omits description/metadata; append `?questions=true` to include fields.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from job_scout.models import ATSJob, ATSProvider, Site
from job_scout.ats.base import ATSAdapter, parse_ats_location

log = logging.getLogger("job_scout.ats.greenhouse")

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


class GreenhouseAdapter(ATSAdapter):
    site = Site.GREENHOUSE
    provider = ATSProvider.GREENHOUSE

    def fetch_jobs(self, client: httpx.Client, slug: str) -> list[ATSJob]:
        url = GREENHOUSE_API.format(slug=slug)
        payload = self._get_json(client, url)
        if not isinstance(payload, dict):
            return []
        items = payload.get("jobs")
        if not isinstance(items, list):
            return []
        jobs: list[ATSJob] = []
        for item in items:
            job = self._parse_item(item)
            if job is not None:
                jobs.append(job)
        return jobs

    def _parse_item(self, item: object) -> ATSJob | None:
        if not isinstance(item, dict):
            return None
        source_id = str(item.get("id") or item.get("requisition_id") or "")
        if not source_id:
            return None
        title = str(item.get("title") or "Untitled")
        location_text = item.get("location") or {}
        if isinstance(location_text, dict):
            location_text = location_text.get("name") or ""
        location = parse_ats_location(str(location_text) if location_text else None)

        absolute_url = item.get("absolute_url") or ""
        updated = _parse_datetime(item.get("updated_at"))
        return ATSJob(
            source=self.site,
            source_id=source_id,
            url=absolute_url,
            apply_url=absolute_url,
            title=title,
            location=location,
            location_text=str(location_text) if location_text else None,
            description="",
            posted_at=_parse_datetime(item.get("updated_at")),
            updated_at=updated or datetime.now(),
            last_seen_at=updated or datetime.now(),
            first_seen_at=datetime.now(),
        )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None