"""Indeed GraphQL API scraper. Adapted from JobSpy (MIT license)."""

from __future__ import annotations

import logging
from datetime import datetime

from job_scout.models import (
    COUNTRIES,
    US_STATES,
    Compensation,
    Job,
    JobType,
    Location,
    ScrapeParams,
    Site,
)
from job_scout.scrapers import BaseScraper
from job_scout.scrapers.constants import (
    INDEED_API_URL,
    INDEED_HEADERS,
    INDEED_SEARCH_QUERY,
)
from job_scout.util import html_to_text, is_remote, parse_compensation_interval

log = logging.getLogger("job_scout.scrapers.indeed")


class IndeedScraper(BaseScraper):
    site = Site.INDEED

    def scrape(self, params: ScrapeParams) -> list[Job]:
        jobs: list[Job] = []
        cursor = None
        seen_urls: set[str] = set()
        pages = 0

        with self._make_client() as client:
            while len(jobs) < params.results_wanted and pages < self.config.max_pages:
                log.info(f"Indeed search page, {len(jobs)} jobs so far")
                page_jobs, cursor = self._scrape_page(client, params, cursor, seen_urls)
                pages += 1
                if not page_jobs:
                    break
                jobs.extend(page_jobs)
                if not cursor:
                    break

        return jobs[: params.results_wanted]

    def _scrape_page(
        self, client, params: ScrapeParams, cursor: str | None, seen_urls: set
    ) -> tuple[list[Job], str | None]:
        search_term = (
            params.search_term.replace('"', '\\"') if params.search_term else ""
        )
        filters_str = ""
        if params.hours_old:
            filters_str = f'filters: {{ date: {{ field: "dateOnIndeed", start: "{params.hours_old}h" }} }}'

        country = self._country_from_location(params.location) or params.country
        radius_unit = "KILOMETERS" if country == "AU" else "MILES"
        query = INDEED_SEARCH_QUERY.format(
            what=f'what: "{search_term}"' if search_term else "",
            location=(
                f'location: {{where: "{params.location}", radius: {params.distance_miles}, radiusUnit: {radius_unit}}}'
                if params.location
                else ""
            ),
            cursor=f'cursor: "{cursor}"' if cursor else "",
            filters=filters_str,
        )

        headers = INDEED_HEADERS.copy()
        headers["indeed-co"] = country

        resp = self._post_with_retry(
            client,
            INDEED_API_URL,
            json={"query": query},
            headers=headers,
        )
        if resp is None or not resp.is_success:
            log.warning(f"Indeed API returned {resp.status_code if resp else 'None'}")
            self._debug_response(resp, "non_success")
            return [], None

        try:
            data = resp.json()
            results = data["data"]["jobSearch"]["results"]
            next_cursor = data["data"]["jobSearch"]["pageInfo"]["nextCursor"]
        except (KeyError, TypeError) as e:
            log.error(f"Indeed response parse error: {e}")
            self._debug_response(resp, "parse_error")
            return [], None

        jobs = []
        for result in results:
            job_data = result.get("job") or result
            job = self._parse_job(job_data, seen_urls, params, country)
            if job:
                jobs.append(job)

        return jobs, next_cursor

    def _parse_job(
        self, job: dict, seen_urls: set, params: ScrapeParams, country: str
    ) -> Job | None:
        key = job.get("key", "")
        indeed_domain = self._indeed_domain(country)
        job_url = f"https://{indeed_domain}/viewjob?jk={key}"
        if job_url in seen_urls:
            return None
        seen_urls.add(job_url)

        # Description
        desc_html = (job.get("description") or {}).get("html", "")
        description = html_to_text(desc_html)

        # Location
        loc = job.get("location") or {}
        location = Location(
            city=loc.get("city"),
            state=loc.get("admin1Code"),
            country=loc.get("countryCode", country),
        )

        # Compensation
        compensation = self._parse_compensation(job.get("compensation") or {})

        # Job type from attributes
        job_types = self._parse_job_types(job.get("attributes") or [])

        # Date posted
        date_posted = None
        ts = job.get("datePublished")
        if ts:
            try:
                date_posted = datetime.fromtimestamp(ts / 1000).date()
            except (ValueError, OSError):
                pass

        # Remote check
        remote = is_remote(
            job.get("title", ""),
            description,
            (loc.get("formatted") or {}).get("long", ""),
        )
        location.is_remote = remote

        # Company
        employer = job.get("employer") or {}
        company = employer.get("name") or "Unknown"

        return Job(
            source=Site.INDEED,
            source_id=key,
            url=job_url,
            title=job.get("title", ""),
            company=company,
            location=location,
            description=description,
            compensation=compensation,
            job_type=job_types,
            date_posted=date_posted,
        )

    @staticmethod
    def _country_from_location(location: str | None) -> str | None:
        """Derive a country code from a location string when it names a country.

        Names only (e.g. "Remote Australia" -> AU, "London UK" -> GB) so plain
        city/state strings fall back to the configured country. US state names
        that collide with country names (Georgia) are excluded.
        """
        if not location:
            return None
        state_names = set(US_STATES)
        for token in location.lower().split():
            token = token.strip(".,")
            if token in COUNTRIES and token not in state_names:
                return COUNTRIES[token]
        return None

    @staticmethod
    def _indeed_domain(country: str) -> str:
        domain_map = {
            "AU": "au.indeed.com",
            "GB": "uk.indeed.com",
            "CA": "ca.indeed.com",
            "IE": "ie.indeed.com",
            "NZ": "nz.indeed.com",
            "SG": "sg.indeed.com",
            "IN": "in.indeed.com",
            "DE": "de.indeed.com",
            "FR": "fr.indeed.com",
            "JP": "jp.indeed.com",
            "BR": "br.indeed.com",
            "MX": "mx.indeed.com",
            "ES": "es.indeed.com",
            "IT": "it.indeed.com",
            "NL": "nl.indeed.com",
            "CH": "ch.indeed.com",
            "AE": "ae.indeed.com",
            "SA": "sa.indeed.com",
            "KR": "kr.indeed.com",
            "SE": "se.indeed.com",
        }
        return domain_map.get(country, "www.indeed.com")

    @staticmethod
    def _parse_compensation(comp: dict) -> Compensation | None:
        base = comp.get("baseSalary")
        if not base:
            estimated = comp.get("estimated") or {}
            base = estimated.get("baseSalary")
        if not base:
            return None

        interval = parse_compensation_interval(base.get("unitOfWork", "YEAR"))
        if not interval:
            return None

        salary_range = base.get("range") or {}
        min_amt = salary_range.get("min")
        max_amt = salary_range.get("max")
        if min_amt is None and max_amt is None:
            return None

        currency = comp.get("currencyCode") or "USD"
        if not currency:
            estimated = comp.get("estimated") or {}
            currency = estimated.get("currencyCode", "USD")

        return Compensation(
            min_amount=int(min_amt) if min_amt is not None else None,
            max_amount=int(max_amt) if max_amt is not None else None,
            currency=currency,
            interval=interval,
        )

    @staticmethod
    def _parse_job_types(attributes: list) -> list[JobType]:
        type_map = {
            "fulltime": JobType.FULL_TIME,
            "parttime": JobType.PART_TIME,
            "contract": JobType.CONTRACT,
            "internship": JobType.INTERNSHIP,
            "temporary": JobType.TEMPORARY,
        }
        types = []
        for attr in attributes:
            label = attr.get("label", "").replace("-", "").replace(" ", "").lower()
            jt = type_map.get(label)
            if jt:
                types.append(jt)
        return types
