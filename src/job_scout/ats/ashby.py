"""Ashby adapter: public job-board posting API.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/<slug>
Returns {"jobs": [ ... ]}. Each job: {"id", "title", "jobUrl", "applyUrl",
"location", "employmentType", "publishedAt", "descriptionHtml", ...}.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from job_scout.models import ATSJob, ATSProvider, Site
from job_scout.ats.base import ATSAdapter, normalize_employment_type, parse_ats_location

log = logging.getLogger("job_scout.ats.ashby")

ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


class AshbyAdapter(ATSAdapter):
    site = Site.ASHBY
    provider = ATSProvider.ASHBY

    def fetch_jobs(self, client: httpx.Client, slug: str) -> list[ATSJob]:
        url = ASHBY_API.format(slug=slug)
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
        source_id = str(item.get("id") or "")
        if not source_id:
            return None
        title = str(item.get("title") or "Untitled")

        location_text = item.get("location") or ""
        if isinstance(location_text, dict):
            # Ashby sometimes nests location as an object; fall back to its name.
            location_text = location_text.get("name") or ""
        location = parse_ats_location(str(location_text) if location_text else None)

        employment_type = normalize_employment_type(item.get("employmentType"))

        posted_at = _parse_datetime(item.get("publishedAt"))
        description_html = str(item.get("descriptionHtml") or "")

        return ATSJob(
            source=self.site,
            source_id=source_id,
            url=str(item.get("jobUrl") or ""),
            apply_url=str(item.get("applyUrl") or item.get("jobUrl") or ""),
            title=title,
            location=location,
            location_text=str(location_text) if location_text else None,
            employment_type=employment_type,
            description_html=description_html,
            description="",
            posted_at=posted_at,
            updated_at=posted_at or datetime.now(),
            last_seen_at=posted_at or datetime.now(),
            first_seen_at=datetime.now(),
        )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None