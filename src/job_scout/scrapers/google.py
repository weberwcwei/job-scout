"""Google Jobs HTML scraper. Adapted from JobSpy (MIT license)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from job_scout.models import Job, Location, ScrapeParams, Site
from job_scout.scrapers import BaseScraper
from job_scout.scrapers.constants import (
    GOOGLE_ASYNC_URL,
    GOOGLE_HEADERS_ASYNC,
    GOOGLE_HEADERS_INITIAL,
    GOOGLE_SEARCH_URL,
)
from job_scout.util import extract_job_types, is_remote

log = logging.getLogger("job_scout.scrapers.google")

# Magic key in Google's nested JSON response that identifies job listing data
GOOGLE_JOB_KEY = "520084652"


class GoogleScraper(BaseScraper):
    site = Site.GOOGLE

    def scrape(self, params: ScrapeParams) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        with self._make_client() as client:
            # First page
            cursor, initial_jobs = self._get_initial_page(client, params, seen_urls)
            jobs.extend(initial_jobs)

            if not cursor:
                return jobs

            # Pagination
            pages = 1  # initial page already fetched
            while len(jobs) < params.results_wanted and cursor and pages < self.config.max_pages:
                log.info(f"Google search page, {len(jobs)} jobs so far")
                page_jobs, cursor = self._get_next_page(client, cursor, seen_urls)
                pages += 1
                if not page_jobs:
                    break
                jobs.extend(page_jobs)

        return jobs[: params.results_wanted]

    def _get_initial_page(
        self, client, params: ScrapeParams, seen_urls: set
    ) -> tuple[str | None, list[Job]]:
        query = f"{params.search_term} jobs"
        if params.location:
            query += f" near {params.location}"
        if params.hours_old:
            if params.hours_old <= 24:
                query += " since yesterday"
            elif params.hours_old <= 72:
                query += " in the last 3 days"
            elif params.hours_old <= 168:
                query += " in the last week"
            else:
                query += " in the last month"

        resp = self._get_with_retry(
            client,
            GOOGLE_SEARCH_URL,
            params={"q": query, "udm": "8"},
            headers=GOOGLE_HEADERS_INITIAL,
        )
        if resp is None or resp.status_code != 200:
            return None, []

        # Extract cursor for pagination
        fc_match = re.search(r'data-async-fc="([^"]+)"', resp.text)
        cursor = fc_match.group(1) if fc_match else None

        # Parse initial page jobs
        jobs = []
        raw_jobs = _find_jobs_initial_page(resp.text)
        for raw in raw_jobs:
            job = self._parse_job(raw, seen_urls)
            if job:
                jobs.append(job)

        return cursor, jobs

    def _get_next_page(
        self, client, cursor: str, seen_urls: set
    ) -> tuple[list[Job], str | None]:
        resp = self._get_with_retry(
            client,
            GOOGLE_ASYNC_URL,
            params={"fc": cursor, "fcv": "3", "async": "_fmt:prog"},
            headers=GOOGLE_HEADERS_ASYNC,
        )
        if resp is None or resp.status_code != 200:
            return [], None

        text = resp.text
        # Extract next cursor
        fc_match = re.search(r'data-async-fc="([^"]+)"', text)
        next_cursor = fc_match.group(1) if fc_match else None

        # Parse jobs from nested JSON
        jobs = []
        try:
            start_idx = text.find("[[[")
            end_idx = text.rindex("]]]") + 3
            if start_idx >= 0 and end_idx > start_idx:
                parsed = json.loads(text[start_idx:end_idx])[0]
                for array in parsed:
                    if len(array) < 2:
                        continue
                    job_data_str = array[1]
                    if not isinstance(job_data_str, str) or not job_data_str.startswith("[[["):
                        continue
                    job_d = json.loads(job_data_str)
                    job_info = _find_job_info(job_d)
                    if job_info:
                        job = self._parse_job(job_info, seen_urls)
                        if job:
                            jobs.append(job)
        except (json.JSONDecodeError, IndexError, ValueError) as e:
            log.error(f"Google pagination parse error: {e}")

        return jobs, next_cursor

    def _parse_job(self, job_info: list, seen_urls: set) -> Job | None:
        try:
            # job_info indices based on Google's response structure
            job_url = job_info[3][0][0] if job_info[3] and job_info[3][0] else None
            if not job_url or job_url in seen_urls:
                return None
            seen_urls.add(job_url)

            title = job_info[0] or ""
            company = job_info[1] or ""
            location_str = job_info[2] or ""
            description = job_info[19] if len(job_info) > 19 else ""
            job_id = str(job_info[28]) if len(job_info) > 28 else job_url

            # Parse location
            city = state = country = None
            if location_str and "," in location_str:
                parts = [p.strip() for p in location_str.split(",")]
                city = parts[0] if parts else None
                state = parts[1] if len(parts) > 1 else None
                country = parts[2] if len(parts) > 2 else None

            # Parse date
            date_posted = None
            days_ago_str = job_info[12] if len(job_info) > 12 else None
            if isinstance(days_ago_str, str):
                match = re.search(r"\d+", days_ago_str)
                if match:
                    days_ago = int(match.group())
                    date_posted = (datetime.now() - timedelta(days=days_ago)).date()

            remote = is_remote(title, description or "", location_str)

            return Job(
                source=Site.GOOGLE,
                source_id=str(job_id),
                url=job_url,
                title=title,
                company=company,
                location=Location(
                    city=city, state=state, country=country, is_remote=remote
                ),
                description=description or "",
                date_posted=date_posted,
                job_type=extract_job_types(description),
            )
        except (IndexError, TypeError) as e:
            log.debug(f"Failed to parse Google job: {e}")
            return None


def _find_job_info(data) -> list | None:
    """Recursively search for job listing data using the magic key."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key == GOOGLE_JOB_KEY and isinstance(value, list):
                return value
            result = _find_job_info(value)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_job_info(item)
            if result:
                return result
    return None


def _find_jobs_initial_page(html_text: str) -> list:
    """Extract job info from initial Google search page HTML."""
    pattern = f'{GOOGLE_JOB_KEY}":(' + r"\[.*?\]\s*])\s*}\s*]\s*]\s*]\s*]\s*]"
    results = []
    for match in re.finditer(pattern, html_text):
        try:
            parsed = json.loads(match.group(1))
            results.append(parsed)
        except json.JSONDecodeError:
            pass
    return results
