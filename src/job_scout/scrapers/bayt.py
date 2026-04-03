"""Bayt.com HTML scraper. International / MENA job market."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from job_scout.models import Job, Location, ScrapeParams, Site
from job_scout.scrapers import BaseScraper
from job_scout.scrapers.constants import BAYT_BASE_URL, BAYT_HEADERS
from job_scout.util import is_remote

log = logging.getLogger("job_scout.scrapers.bayt")


class BaytScraper(BaseScraper):
    site = Site.BAYT

    def scrape(self, params: ScrapeParams) -> list[Job]:
        jobs: list[Job] = []
        page = 1

        with self._make_client() as client:
            while len(jobs) < params.results_wanted and page <= self.config.max_pages:
                log.info(f"Bayt search page {page}, {len(jobs)} jobs so far")
                page_jobs = self._scrape_page(client, params, page)
                if not page_jobs:
                    break
                jobs.extend(page_jobs)
                page += 1

        return jobs[: params.results_wanted]

    def _scrape_page(
        self, client, params: ScrapeParams, page: int
    ) -> list[Job]:
        search_slug = quote_plus(params.search_term).replace("+", "-")
        url = f"{BAYT_BASE_URL}/en/international/jobs/{search_slug}-jobs/"
        query_params = {"page": page}
        if params.location:
            query_params["location"] = params.location

        resp = self._get_with_retry(
            client,
            url,
            params=query_params,
            headers=BAYT_HEADERS,
        )
        if resp is None or resp.status_code != 200:
            log.warning(f"Bayt returned {resp.status_code if resp else 'None'}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("li", attrs={"data-js-job": True})
        if not cards:
            # Try alternative selectors
            cards = soup.find_all("div", class_=re.compile(r"job-item|has-pointer-d"))

        jobs = []
        for card in cards:
            job = self._parse_card(card)
            if job:
                jobs.append(job)
        return jobs

    def _parse_card(self, card) -> Job | None:
        # Job ID
        job_id = card.get("data-job-id") or card.get("data-js-job", "")
        if not job_id:
            # Try to extract from link
            link_tag = card.find("a", href=re.compile(r"/en/.*job-\d+"))
            if link_tag:
                match = re.search(r"job-(\d+)", link_tag.get("href", ""))
                if match:
                    job_id = match.group(1)
        if not job_id or self._is_dup(str(job_id)):
            return None

        # Title
        title_tag = card.find("h2") or card.find("a", class_=re.compile(r"jb-title|job-title"))
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            return None

        # URL
        link_tag = card.find("a", href=True)
        href = link_tag["href"] if link_tag else ""
        if href and not href.startswith("http"):
            href = f"{BAYT_BASE_URL}{href}"

        # Company
        company_tag = card.find("b", class_=re.compile(r"company|employer")) or \
                      card.find("span", class_=re.compile(r"company|employer"))
        if not company_tag:
            company_tag = card.find("div", class_=re.compile(r"company"))
        company = company_tag.get_text(strip=True) if company_tag else "Unknown"

        # Location
        loc_tag = card.find("span", class_=re.compile(r"location|loc"))
        if not loc_tag:
            loc_tag = card.find("div", class_=re.compile(r"location"))
        location_str = loc_tag.get_text(strip=True) if loc_tag else ""
        city = state = country = None
        if location_str and "," in location_str:
            parts = [p.strip() for p in location_str.split(",")]
            city = parts[0]
            country = parts[-1] if len(parts) > 1 else None
        elif location_str:
            city = location_str

        remote = is_remote(title, "", location_str)

        # Date
        date_posted = None
        date_tag = card.find("span", class_=re.compile(r"date|time|posted"))
        if date_tag:
            date_text = date_tag.get_text(strip=True)
            match = re.search(r"(\d+)", date_text)
            if match:
                days = int(match.group(1))
                if "hour" in date_text.lower():
                    days = 0
                date_posted = (datetime.now() - timedelta(days=days)).date()

        return Job(
            source=Site.BAYT,
            source_id=str(job_id),
            url=href,
            title=title,
            company=company,
            location=Location(
                city=city, state=state, country=country, is_remote=remote
            ),
            date_posted=date_posted,
        )
