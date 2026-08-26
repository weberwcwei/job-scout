"""Tests for company resolution (M3): domain -> careers -> ATS -> slug."""

from __future__ import annotations

import httpx
import pytest
import respx

from job_scout.config import ScrapingConfig
from job_scout.models import ATSProvider, Company


@pytest.fixture
def config():
    return ScrapingConfig(
        delay_min_seconds=0,
        delay_max_seconds=0,
        max_retries=0,
        min_request_interval_seconds=0,
        request_timeout=5,
    )


class TestDetectATS:
    def test_extracts_slug_from_board_url(self, config):
        from job_scout.registry.companies import CompanyResolver

        resolver = CompanyResolver(config)
        company = Company(name="Canva", domain="canva.com")
        with respx.mock:
            respx.get(url__startswith="https://canva.com").mock(
                return_value=httpx.Response(
                    200, text='<a href="https://boards.greenhouse.io/canva/jobs/1">Jobs</a>'
                )
            )
            company.careers_url = "https://canva.com/careers"
            resolver._detect_ats(company)

        assert company.ats == ATSProvider.GREENHOUSE
        assert company.ats_slug == "canva"

    def test_signature_fallback_no_slug(self, config):
        from job_scout.registry.companies import CompanyResolver

        resolver = CompanyResolver(config)
        company = Company(name="Acme", domain="acme.com")
        with respx.mock:
            respx.get("https://acme.com/careers").mock(
                return_value=httpx.Response(
                    200, text="<p>Powered by greenhouse.io</p>"
                )
            )
            company.careers_url = "https://acme.com/careers"
            resolver._detect_ats(company)

        assert company.ats == ATSProvider.GREENHOUSE
        assert company.ats_slug is None

    def test_no_detection_leaves_unknown(self, config):
        from job_scout.registry.companies import CompanyResolver

        resolver = CompanyResolver(config)
        company = Company(name="Acme", domain="acme.com")
        with respx.mock:
            respx.get("https://acme.com/careers").mock(
                return_value=httpx.Response(200, text="<p>nothing here</p>")
            )
            company.careers_url = "https://acme.com/careers"
            resolver._detect_ats(company)

        assert company.ats == ATSProvider.UNKNOWN
        assert company.ats_slug is None


class TestFindCareersUrl:
    def test_common_path_match(self, config):
        from job_scout.registry.companies import CompanyResolver

        resolver = CompanyResolver(config)
        with respx.mock:
            respx.get("https://acme.com/careers").mock(
                return_value=httpx.Response(200, text="ok")
            )
            # Domain guesses would also be checked first; stub them.
            respx.get(url__startswith="https://acme").mock(
                return_value=httpx.Response(404)
            )
            respx.get("https://acme.com/careers").mock(
                return_value=httpx.Response(200, text="careers")
            )
            url = resolver._find_careers_url("acme.com")
        assert url == "https://acme.com/careers"

    def test_homepage_link_scan(self, config):
        from job_scout.registry.companies import CompanyResolver

        resolver = CompanyResolver(config)
        with respx.mock:
            # Common paths 404.
            respx.get(url__startswith="https://acme.com/career").mock(return_value=httpx.Response(404))
            respx.get(url__startswith="https://acme.com/jobs").mock(return_value=httpx.Response(404))
            respx.get("https://acme.com/about/careers").mock(return_value=httpx.Response(404))
            respx.get("https://acme.com/company/careers").mock(return_value=httpx.Response(404))
            respx.get("https://acme.com").mock(
                return_value=httpx.Response(
                    200, text='<a href="/work-with-us">Work with us</a>'
                )
            )
            respx.get("https://acme.com/work-with-us").mock(
                return_value=httpx.Response(200, text="hello")
            )
            url = resolver._find_careers_url("acme.com")
        assert url == "https://acme.com/work-with-us"


class TestResolve:
    def test_resolve_fills_fields(self, config):
        import re

        from job_scout.registry.companies import CompanyResolver

        resolver = CompanyResolver(config)
        company = Company(name="Canva")
        with respx.mock:
            # Exact-path routes: respx treats a bare URL as a path wildcard,
            # so anchor each route to its exact path with regex.
            respx.get(re.compile(r"https://canva\.com\.au/?$")).mock(
                return_value=httpx.Response(200, text="home")
            )
            respx.get("https://canva.com.au/careers").mock(
                return_value=httpx.Response(
                    200, text='<a href="https://boards.greenhouse.io/canva">jobs</a>'
                )
            )
            resolved = resolver.resolve(company)

        assert resolved.domain == "canva.com.au"
        assert resolved.ats == ATSProvider.GREENHOUSE
        assert resolved.ats_slug == "canva"


class TestHelpers:
    def test_slugify(self):
        from job_scout.registry.companies import _slugify

        assert _slugify("Acme Corp") == "acmecorp"
        assert _slugify("Foo & Bar Pty Ltd") == "foobarptyltd"

    def test_absolutize(self):
        from job_scout.registry.companies import _absolutize

        assert _absolutize("https://acme.com", "/jobs") == "https://acme.com/jobs"
        assert _absolutize("https://acme.com", "https://x.com/y") == "https://x.com/y"
        assert _absolutize("https://acme.com", "relative") is None