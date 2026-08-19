"""Jora AU scraper. Server-rendered HTML (BeautifulSoup), no JS required."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

from job_scout.models import (
    Compensation,
    Job,
    JobType,
    Location,
    ScrapeParams,
    Site,
)
from job_scout.scrapers import BaseScraper
from job_scout.scrapers.constants import (
    JORA_BASE_URL,
    JORA_HEADERS,
    JORA_SEARCH_URL,
)
from job_scout.util import (
    currency_parser,
    extract_job_types,
    html_to_text,
    is_remote,
    parse_compensation_interval,
)

log = logging.getLogger("job_scout.scrapers.jora")


class JoraScraper(BaseScraper):
    site = Site.JORA
    jobs_per_page = 15

    def scrape(self, params: ScrapeParams) -> list[Job]:
        jobs: list[Job] = []
        page = 1

        with self._make_client() as client:
            while (
                len(jobs) < params.results_wanted
                and page <= self.config.max_pages
            ):
                log.info(f"Jora search page {page}, {len(jobs)} jobs so far")
                page_jobs = self._scrape_page(client, params, page)
                page += 1
                if not page_jobs:
                    break
                jobs.extend(page_jobs)

        return jobs[: params.results_wanted]

    def _scrape_page(
        self, client, params: ScrapeParams, page: int
    ) -> list[Job]:
        query_params = {
            "q": params.search_term,
            "l": params.location,
        }
        if page > 1:
            query_params["p"] = page

        resp = self._get_with_retry(
            client,
            JORA_SEARCH_URL,
            params=query_params,
            headers=JORA_HEADERS,
        )
        if resp is None or resp.status_code != 200:
            self._debug_response(resp, "non_success")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("article.job-card, div.job-card")
        if not cards:
            return []

        jobs = []
        for card in cards:
            job = self._parse_card(client, card)
            if job:
                jobs.append(job)

        return jobs

    def _parse_card(self, client, card) -> Job | None:
        title_a = card.select_one(".job-title a")
        if not title_a or "href" not in title_a.attrs:
            return None
        href = title_a["href"].split("?")[0]
        job_id = href.split("-")[-1] if "-" in href else href
        if self._is_dup(job_id):
            return None

        title = title_a.get_text(strip=True)

        company_tag = card.select_one(".job-company")
        company = company_tag.get_text(strip=True) if company_tag else "Unknown"

        loc_tag = card.select_one(".job-location")
        location = (
            self._parse_location(loc_tag.get_text(" ", strip=True))
            if loc_tag
            else Location(country="AU")
        )

        date_tag = card.select_one(".job-listed-date")
        date_posted = (
            self._parse_date(date_tag.get_text(" ", strip=True)) if date_tag else None
        )

        badges = [
            b.get_text(" ", strip=True)
            for b in card.select(".badges .badge .content")
        ]
        compensation = self._parse_compensation(badges)
        job_types = self._parse_job_types(badges)

        description = "\n".join(
            li.get_text(" ", strip=True) for li in card.select(".job-abstract li")
        )
        # Jora's detail pages 403 for plain httpx (Cloudflare) but pass with
        # curl_cffi browser impersonation, so only fetch when TLS is enabled.
        if self.config.fetch_descriptions and self.config.use_tls_fingerprinting:
            full = self._fetch_description(client, href, job_id)
            if full:
                description = full

        remote = is_remote(title, description, location.display)
        location.is_remote = remote

        return Job(
            source=Site.JORA,
            source_id=job_id,
            url=f"{JORA_BASE_URL}{href}",
            title=title,
            company=company,
            location=location,
            description=description,
            compensation=compensation,
            job_type=job_types,
            date_posted=date_posted,
        )

    def _fetch_description(self, client, href: str, job_id: str) -> str:
        key = (Site.JORA.value, job_id)
        if self.description_cache is not None:
            return self.description_cache.get_or_fetch(
                key, lambda: self._fetch_description_uncached(client, href)
            )
        return self._fetch_description_uncached(client, href) or ""

    def _fetch_description_uncached(self, client, href: str) -> str | None:
        resp = self._get_with_retry(
            client, f"{JORA_BASE_URL}{href}", headers=JORA_HEADERS
        )
        description = ""
        if resp is not None and resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for selector in (
                "#job-description-container",
                '[data-testid="job-description"]',
                ".job-description-container",
                ".job-description",
                ".job-ad-text",
                ".job-view-body",
            ):
                node = soup.select_one(selector)
                if node:
                    description = html_to_text(str(node))
                    break
        return description or None

    @staticmethod
    def _parse_location(text: str) -> Location:
        if not text:
            return Location(country="AU")
        if "remote" in text.lower():
            return Location(is_remote=True, country="AU")
        if "," in text:
            parts = [p.strip() for p in text.split(",")]
            return Location(
                city=parts[0],
                state=parts[1] if len(parts) > 1 else None,
                country="AU",
            )
        tokens = text.split()
        if (
            len(tokens) >= 2
            and re.match(r"^[A-Z]{2,3}$", tokens[-1])
        ):
            return Location(
                city=" ".join(tokens[:-1]),
                state=tokens[-1],
                country="AU",
            )
        return Location(city=text, country="AU")

    @staticmethod
    def _parse_date(text: str) -> date | None:
        text = text.lower()
        now = date.today()
        m = re.search(r"(\d+)\s*(h|hr|hour)", text)
        if m:
            return now
        m = re.search(r"(\d+)\s*(d|day)", text)
        if m:
            return now - timedelta(days=int(m.group(1)))
        m = re.search(r"(\d+)\s*(w|week)", text)
        if m:
            return now - timedelta(weeks=int(m.group(1)))
        if "today" in text:
            return now
        return None

    @staticmethod
    def _parse_compensation(badges: list[str]) -> Compensation | None:
        text = " ".join(badges)
        numbers = re.findall(r"\$[\d,]+(?:\.\d+)?", text)
        if not numbers:
            return None
        values = [currency_parser(n) for n in numbers]
        interval = None
        m = re.search(r"per\s+(year|hour|week|day|month)|(?:a|an)\s+(year|hour|week|day|month)", text, re.I)
        if m:
            interval = parse_compensation_interval(m.group(1) or m.group(2))
        min_amount = min(values)
        max_amount = max(values) if len(values) > 1 else None
        return Compensation(
            min_amount=int(min_amount),
            max_amount=int(max_amount) if max_amount else None,
            currency="AUD",
            interval=interval,
        )

    @staticmethod
    def _parse_job_types(badges: list[str]) -> list[JobType]:
        text = " ".join(badges)
        types = extract_job_types(text)
        if re.search(r"casual", text, re.I):
            types.append(JobType.TEMPORARY)
        if re.search(r"permanent", text, re.I) and not types:
            types.append(JobType.FULL_TIME)
        return types
