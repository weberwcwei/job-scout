"""Tests for the ATS adapters (M1): Greenhouse, Lever, Ashby.

Uses respx to mock the public JSON endpoints; no network access.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from job_scout.config import ScrapingConfig
from job_scout.models import ATSProvider, Company, Site


@pytest.fixture
def config():
    return ScrapingConfig(
        delay_min_seconds=0,
        delay_max_seconds=0,
        max_retries=0,
        min_request_interval_seconds=0,
    )


def _company() -> Company:
    return Company(
        id=1,
        name="Example",
        domain="example.com",
        ats=ATSProvider.GREENHOUSE,
        ats_slug="example",
    )


class TestGreenhouseAdapter:
    def test_parses_jobs(self, config):
        from job_scout.ats.greenhouse import GreenhouseAdapter

        payload = {
            "jobs": [
                {
                    "id": 12345,
                    "title": "Software Engineer",
                    "absolute_url": "https://boards.greenhouse.io/example/jobs/12345",
                    "location": {"name": "Sydney, NSW"},
                    "content": "<p>Build things with <b>Python</b>.</p>",
                    "updated_at": "2026-08-25T09:00:00Z",
                }
            ]
        }
        adapter = GreenhouseAdapter(config)
        with respx.mock:
            respx.get(
                "https://boards-api.greenhouse.io/v1/boards/example/jobs",
                params={"questions": "true"},
            ).mock(return_value=httpx.Response(200, json=payload))
            jobs = adapter.fetch_jobs(adapter._make_client(), "example")

        assert len(jobs) == 1
        job = jobs[0]
        assert job.source == Site.GREENHOUSE
        assert job.source_id == "12345"
        assert job.title == "Software Engineer"
        assert job.location.city == "Sydney"
        assert job.location.state == "NSW"
        assert job.location.country == "AU"
        assert job.url.startswith("https://boards.greenhouse.io/example")
        assert job.description_html == "<p>Build things with <b>Python</b>.</p>"
        assert "Python" in job.description


class TestLeverAdapter:
    def test_parses_jobs(self, config):
        from job_scout.ats.lever import LeverAdapter

        payload = [
            {
                "id": "abc-def",
                "text": "Backend Engineer",
                "hostedUrl": "https://jobs.lever.co/example/abc-def",
                "applyUrl": "https://jobs.lever.co/example/abc-def/apply",
                "categories": {
                    "location": "Melbourne, VIC",
                    "commitment": "Full-time",
                    "team": "Engineering",
                },
                "descriptionPlain": "Backend role using Go and Postgres.",
                "createdAt": 1724562000000,
            }
        ]
        adapter = LeverAdapter(config)
        with respx.mock:
            respx.get("https://api.lever.co/v0/postings/example?mode=json").mock(
                return_value=httpx.Response(200, json=payload)
            )
            jobs = adapter.fetch_jobs(adapter._make_client(), "example")

        assert len(jobs) == 1
        job = jobs[0]
        assert job.source == Site.LEVER
        assert job.source_id == "abc-def"
        assert job.location.city == "Melbourne"
        assert job.location.state == "VIC"
        assert job.employment_type == "full_time"
        assert job.posted_at is not None
        assert "Go" in job.description


class TestAshbyAdapter:
    def test_parses_jobs(self, config):
        from job_scout.ats.ashby import AshbyAdapter

        payload = {
            "jobs": [
                {
                    "id": "job-1",
                    "title": "Product Manager",
                    "jobUrl": "https://jobs.ashbyhq.com/example/job-1",
                    "applyUrl": "https://jobs.ashbyhq.com/example/job-1/apply",
                    "location": "Sydney, NSW",
                    "employmentType": "Full-time",
                    "publishedAt": "2026-08-20T10:00:00Z",
                    "descriptionHtml": "<p>Desc</p>",
                }
            ]
        }
        adapter = AshbyAdapter(config)
        with respx.mock:
            respx.get("https://api.ashbyhq.com/posting-api/job-board/example").mock(
                return_value=httpx.Response(200, json=payload)
            )
            jobs = adapter.fetch_jobs(adapter._make_client(), "example")

        assert len(jobs) == 1
        job = jobs[0]
        assert job.source == Site.ASHBY
        assert job.location.city == "Sydney"
        assert job.employment_type == "full_time"
        assert job.description_html == "<p>Desc</p>"
        assert job.apply_url.endswith("/apply")


class TestAdapterRegistry:
    def test_all_three_registered(self):
        from job_scout.ats import get_adapter, get_supported_providers

        providers = get_supported_providers()
        assert ATSProvider.GREENHOUSE in providers
        assert ATSProvider.LEVER in providers
        assert ATSProvider.ASHBY in providers
        assert get_adapter(ATSProvider.GREENHOUSE) is not None

    def test_unknown_provider_returns_none(self):
        from job_scout.ats import get_adapter

        assert get_adapter(ATSProvider.WORKDAY) is None
