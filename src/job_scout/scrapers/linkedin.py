"""LinkedIn guest API scraper. Adapted from JobSpy (MIT license)."""

from __future__ import annotations

import logging
from datetime import datetime

from bs4 import BeautifulSoup

from job_scout.models import Compensation, Job, Location, ScrapeParams, Site
from job_scout.scrapers import BaseScraper
from job_scout.scrapers.constants import (
    LINKEDIN_HEADERS,
    LINKEDIN_JOB_URL,
    LINKEDIN_SEARCH_URL,
)
from job_scout.util import currency_parser, html_to_text, is_remote

log = logging.getLogger("job_scout.scrapers.linkedin")


class LinkedInScraper(BaseScraper):
    site = Site.LINKEDIN
    jobs_per_page = 25

    def scrape(self, params: ScrapeParams) -> list[Job]:
        jobs: list[Job] = []
        start = 0
        seconds_old = params.hours_old * 3600 if params.hours_old else None

        with self._make_client() as client:
            while len(jobs) < params.results_wanted and start < self.config.max_pages * self.jobs_per_page:
                log.info(f"LinkedIn search page offset={start}")
                query_params = {
                    "keywords": params.search_term,
                    "location": params.location,
                    "distance": params.distance_miles,
                    "start": start,
                }
                if seconds_old is not None:
                    query_params["f_TPR"] = f"r{seconds_old}"

                resp = self._get_with_retry(
                    client,
                    LINKEDIN_SEARCH_URL,
                    params=query_params,
                    headers=LINKEDIN_HEADERS,
                )
                if resp is None or resp.status_code != 200:
                    if resp and resp.status_code == 429:
                        log.warning("LinkedIn rate limited, stopping pagination")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("div", class_="base-search-card")
                if not cards:
                    break

                for card in cards:
                    if len(jobs) >= params.results_wanted:
                        break
                    job = self._parse_card(card, client, fetch_description=True)
                    if job:
                        jobs.append(job)

                start += len(cards)

        return jobs

    def _parse_card(
        self, card, client, fetch_description: bool = False
    ) -> Job | None:
        href_tag = card.find("a", class_="base-card__full-link")
        if not href_tag or "href" not in href_tag.attrs:
            return None
        href = href_tag["href"].split("?")[0]
        job_id = href.split("-")[-1]
        if self._is_dup(job_id):
            return None

        # Title
        title_tag = card.find("span", class_="sr-only")
        title = title_tag.get_text(strip=True) if title_tag else "N/A"

        # Company
        company_tag = card.find("h4", class_="base-search-card__subtitle")
        company_a = company_tag.find("a") if company_tag else None
        company = company_a.get_text(strip=True) if company_a else "N/A"

        # Location
        metadata = card.find("div", class_="base-search-card__metadata")
        location = self._parse_location(metadata)

        # Date posted
        date_posted = None
        if metadata:
            dt_tag = metadata.find("time", class_="job-search-card__listdate")
            if not dt_tag:
                dt_tag = metadata.find("time", class_="job-search-card__listdate--new")
            if dt_tag and "datetime" in dt_tag.attrs:
                try:
                    date_posted = datetime.strptime(dt_tag["datetime"], "%Y-%m-%d").date()
                except ValueError:
                    pass

        # Salary
        compensation = None
        salary_tag = card.find("span", class_="job-search-card__salary-info")
        if salary_tag:
            compensation = self._parse_salary(salary_tag.get_text(separator=" ").strip())

        # Description (optional, requires extra request)
        description = ""
        if fetch_description:
            description = self._fetch_description(client, job_id)

        remote = is_remote(title, description, location.display)

        return Job(
            source=Site.LINKEDIN,
            source_id=job_id,
            url=f"{LINKEDIN_JOB_URL}/{job_id}",
            title=title,
            company=company,
            location=Location(
                city=location.city,
                state=location.state,
                country=location.country,
                is_remote=remote,
            ),
            description=description,
            compensation=compensation,
            date_posted=date_posted,
        )

    def _fetch_description(self, client, job_id: str) -> str:
        resp = self._get_with_retry(
            client, f"{LINKEDIN_JOB_URL}/{job_id}", headers=LINKEDIN_HEADERS
        )
        if resp is None or resp.status_code != 200:
            return ""
        if "linkedin.com/signup" in str(resp.url):
            log.warning("LinkedIn redirecting to signup, description unavailable")
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        div = soup.find(
            "div", class_=lambda x: x and "show-more-less-html__markup" in x
        )
        return html_to_text(str(div)) if div else ""

    @staticmethod
    def _parse_location(metadata) -> Location:
        if not metadata:
            return Location()
        loc_tag = metadata.find("span", class_="job-search-card__location")
        if not loc_tag:
            return Location()
        loc_str = loc_tag.text.strip()
        parts = [p.strip() for p in loc_str.split(",")]
        if len(parts) == 2:
            return Location(city=parts[0], state=parts[1])
        elif len(parts) >= 3:
            return Location(city=parts[0], state=parts[1], country=parts[2])
        return Location(city=loc_str)

    @staticmethod
    def _parse_salary(salary_text: str) -> Compensation | None:
        try:
            values = [currency_parser(v) for v in salary_text.split("-")]
            if len(values) >= 2:
                return Compensation(
                    min_amount=int(values[0]),
                    max_amount=int(values[1]),
                    currency="USD",
                )
        except (ValueError, IndexError):
            pass
        return None
