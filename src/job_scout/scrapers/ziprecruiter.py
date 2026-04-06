"""ZipRecruiter REST API scraper. US & Canada focused."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from job_scout.models import (
    Compensation,
    CompInterval,
    Job,
    Location,
    ScrapeParams,
    Site,
)
from job_scout.scrapers import BaseScraper
from job_scout.scrapers.constants import ZIPRECRUITER_API_URL, ZIPRECRUITER_HEADERS
from job_scout.util import html_to_text, is_remote

log = logging.getLogger("job_scout.scrapers.ziprecruiter")


class ZipRecruiterScraper(BaseScraper):
    site = Site.ZIPRECRUITER

    def scrape(self, params: ScrapeParams) -> list[Job]:
        jobs: list[Job] = []
        continue_from = None
        pages = 0

        with self._make_client() as client:
            while len(jobs) < params.results_wanted and pages < self.config.max_pages:
                log.info(
                    f"ZipRecruiter search page {pages + 1}, {len(jobs)} jobs so far"
                )
                page_jobs, continue_from = self._scrape_page(
                    client, params, continue_from
                )
                pages += 1
                if not page_jobs:
                    break
                jobs.extend(page_jobs)
                if not continue_from:
                    break

        return jobs[: params.results_wanted]

    def _scrape_page(
        self, client, params: ScrapeParams, continue_from: str | None
    ) -> tuple[list[Job], str | None]:
        query_params = {
            "search": params.search_term,
            "location": params.location,
            "radius_miles": params.distance_miles,
            "jobs_per_page": 20,
        }
        if params.hours_old:
            query_params["days_ago"] = max(1, params.hours_old // 24)
        if continue_from:
            query_params["continue_from"] = continue_from

        resp = self._get_with_retry(
            client,
            ZIPRECRUITER_API_URL,
            params=query_params,
            headers=ZIPRECRUITER_HEADERS,
        )
        if resp is None or resp.status_code != 200:
            log.warning(
                f"ZipRecruiter API returned {resp.status_code if resp else 'None'}"
            )
            return [], None

        try:
            data = resp.json()
            job_list = data.get("jobs", [])
            next_token = data.get("continue_from")
        except (ValueError, KeyError) as e:
            log.error(f"ZipRecruiter response parse error: {e}")
            return [], None

        jobs = []
        for job_data in job_list:
            job = self._parse_job(job_data)
            if job:
                jobs.append(job)

        return jobs, next_token

    def _parse_job(self, data: dict) -> Job | None:
        job_id = data.get("id", "")
        if not job_id or self._is_dup(str(job_id)):
            return None

        title = data.get("name", "")
        company_data = data.get("hiring_company") or {}
        company = company_data.get("name", "Unknown")
        job_url = data.get("url", "")

        # Location
        city = data.get("job_city")
        state = data.get("job_state")
        country = data.get("job_country", "US")
        location_str = f"{city}, {state}" if city and state else city or state or ""

        # Description
        snippet = data.get("snippet", "")
        description = html_to_text(snippet) if snippet else ""

        # Remote
        remote = is_remote(title, description, location_str)

        # Date posted
        date_posted = None
        posted_str = data.get("posted_time")
        if posted_str:
            try:
                date_posted = datetime.fromisoformat(
                    posted_str.replace("Z", "+00:00")
                ).date()
            except (ValueError, TypeError):
                pass
        if not date_posted:
            days_ago = data.get("posted_time_friendly", "")
            if "day" in days_ago:
                try:
                    n = int("".join(c for c in days_ago if c.isdigit()) or "0")
                    date_posted = (datetime.now() - timedelta(days=n)).date()
                except ValueError:
                    pass

        # Compensation
        compensation = self._parse_compensation(data)

        return Job(
            source=Site.ZIPRECRUITER,
            source_id=str(job_id),
            url=job_url,
            title=title,
            company=company,
            location=Location(
                city=city, state=state, country=country, is_remote=remote
            ),
            description=description,
            compensation=compensation,
            date_posted=date_posted,
        )

    @staticmethod
    def _parse_compensation(data: dict) -> Compensation | None:
        sal_min = data.get("salary_min_annual") or data.get("salary_min")
        sal_max = data.get("salary_max_annual") or data.get("salary_max")
        if sal_min is None and sal_max is None:
            return None

        # Determine interval from salary source
        source = (data.get("salary_source") or "").lower()
        if "hour" in source:
            interval = CompInterval.HOURLY
        else:
            interval = CompInterval.YEARLY

        return Compensation(
            min_amount=float(sal_min) if sal_min else None,
            max_amount=float(sal_max) if sal_max else None,
            currency="USD",
            interval=interval,
        )
