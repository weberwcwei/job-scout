"""Glassdoor GraphQL API scraper."""

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
from job_scout.scrapers.constants import (
    GLASSDOOR_API_URL,
    GLASSDOOR_BASE_URL,
    GLASSDOOR_HEADERS,
    GLASSDOOR_SEARCH_QUERY,
)
from job_scout.util import html_to_text, is_remote

log = logging.getLogger("job_scout.scrapers.glassdoor")


class GlassdoorScraper(BaseScraper):
    site = Site.GLASSDOOR

    def scrape(self, params: ScrapeParams) -> list[Job]:
        jobs: list[Job] = []
        page = 1

        with self._make_client() as client:
            # Get CSRF token from initial page load
            token = self._get_csrf_token(client)
            headers = GLASSDOOR_HEADERS.copy()
            if token:
                headers["gd-csrf-token"] = token

            while len(jobs) < params.results_wanted and page <= self.config.max_pages:
                log.info(f"Glassdoor search page {page}, {len(jobs)} jobs so far")
                page_jobs = self._scrape_page(client, params, page, headers)
                if not page_jobs:
                    break
                jobs.extend(page_jobs)
                page += 1

        return jobs[: params.results_wanted]

    def _get_csrf_token(self, client) -> str | None:
        resp = self._get_with_retry(
            client,
            GLASSDOOR_BASE_URL,
            headers={"user-agent": GLASSDOOR_HEADERS["user-agent"]},
        )
        if resp is None:
            return None
        # Token is often in cookies or response headers
        cookies = getattr(resp, "cookies", None)
        if cookies:
            for name in ("gdToken", "gd-csrf-token", "GSESSIONID"):
                val = cookies.get(name)
                if val:
                    return val
        return "Ft6oHEKlklxhxgwPF6D-M2LNnEO0O0ZO4Q=="  # fallback public token

    def _scrape_page(
        self, client, params: ScrapeParams, page: int, headers: dict
    ) -> list[Job]:
        date_filter = ""
        if params.hours_old:
            days = max(1, params.hours_old // 24)
            date_filter = f'{{filterKey: "fromAge", values: "{days}d"}}'

        query = GLASSDOOR_SEARCH_QUERY.format(
            keyword=params.search_term.replace('"', '\\"'),
            location=params.location.replace('"', '\\"'),
            page=page,
            date_filter=date_filter,
        )

        resp = self._post_with_retry(
            client,
            GLASSDOOR_API_URL,
            json=[{"operationName": "JobSearchQuery", "query": query, "variables": {}}],
            headers=headers,
        )
        if resp is None or not getattr(
            resp, "is_success", resp.status_code < 300 if resp else False
        ):
            log.warning(
                f"Glassdoor API returned {resp.status_code if resp else 'None'}"
            )
            return []

        try:
            data = resp.json()
            if isinstance(data, list):
                data = data[0]
            listings = data["data"]["jobListings"]["jobListings"]
        except (KeyError, TypeError, IndexError) as e:
            log.error(f"Glassdoor response parse error: {e}")
            return []

        jobs = []
        for listing in listings:
            job = self._parse_listing(listing)
            if job:
                jobs.append(job)
        return jobs

    def _parse_listing(self, listing: dict) -> Job | None:
        try:
            jobview = listing.get("jobview") or listing
            header = jobview.get("header", {})
            job_data = jobview.get("job", {})
            overview = jobview.get("overview", {})

            listing_id = str(job_data.get("listingId", ""))
            if not listing_id or self._is_dup(listing_id):
                return None

            title = header.get("jobTitleText", "")
            company = header.get("employerNameFromSearch") or overview.get(
                "name", "Unknown"
            )
            job_link = header.get("jobLink", "")
            if job_link and not job_link.startswith("http"):
                job_link = f"{GLASSDOOR_BASE_URL}{job_link}"

            # Location
            location_str = jobview.get("locationName", "")
            city = state = country = None
            if location_str and "," in location_str:
                parts = [p.strip() for p in location_str.split(",")]
                city = parts[0] if parts else None
                state = parts[1] if len(parts) > 1 else None
                country = parts[2] if len(parts) > 2 else "US"
            elif location_str:
                city = location_str

            # Remote detection
            remote_types = jobview.get("remoteWorkTypes") or []
            remote = bool(remote_types) or is_remote(title, "", location_str)

            # Description
            description = html_to_text(job_data.get("description", ""))

            # Date posted
            date_posted = None
            age_in_days = header.get("ageInDays")
            if age_in_days is not None:
                try:
                    date_posted = (
                        datetime.now() - timedelta(days=int(age_in_days))
                    ).date()
                except (ValueError, TypeError):
                    pass

            # Compensation
            compensation = self._parse_compensation(header)

            return Job(
                source=Site.GLASSDOOR,
                source_id=listing_id,
                url=job_link or f"{GLASSDOOR_BASE_URL}/job-listing/{listing_id}",
                title=title,
                company=company,
                location=Location(
                    city=city, state=state, country=country or "US", is_remote=remote
                ),
                description=description,
                compensation=compensation,
                date_posted=date_posted,
            )
        except (KeyError, TypeError) as e:
            log.debug(f"Failed to parse Glassdoor listing: {e}")
            return None

    @staticmethod
    def _parse_compensation(header: dict) -> Compensation | None:
        pay_low = header.get("payPercentile10")
        pay_high = header.get("payPercentile90")
        if pay_low is None and pay_high is None:
            return None

        period_map = {
            "ANNUAL": CompInterval.YEARLY,
            "HOURLY": CompInterval.HOURLY,
            "MONTHLY": CompInterval.MONTHLY,
            "WEEKLY": CompInterval.WEEKLY,
        }
        interval = period_map.get(
            header.get("payPeriod", "").upper(), CompInterval.YEARLY
        )
        currency = header.get("payCurrency", "USD")

        return Compensation(
            min_amount=float(pay_low) if pay_low else None,
            max_amount=float(pay_high) if pay_high else None,
            currency=currency or "USD",
            interval=interval,
        )
