"""Tests for the ATS discovery sources (M2)."""

from __future__ import annotations

import httpx
import pytest
import respx

from job_scout.config import DiscoveryConfig
from job_scout.models import ATSProvider


@pytest.fixture
def config():
    cfg = DiscoveryConfig(enabled=True)
    cfg.vc_portfolios = ["blackbird"]
    cfg.funding_sources = ["startupdaily"]
    cfg.ats_search_enabled = True
    return cfg


class TestHelpers:
    def test_clean_company_name(self):
        from job_scout.discovery.constants import clean_company_name

        assert clean_company_name("  Acme   Corp  ") == "Acme Corp"

    def test_extract_domain(self):
        from job_scout.discovery.constants import extract_domain

        assert extract_domain("https://www.acme.com/jobs") == "acme.com"
        assert extract_domain("https://acme.com") == "acme.com"
        assert extract_domain("not a url") == ""

    def test_company_from_headline(self):
        from job_scout.discovery.constants import company_from_headline

        assert company_from_headline("Canva raises $50m to expand") == "Canva"
        assert company_from_headline("AirTree closes $20m fund") == "AirTree"

    def test_looks_like_company_rejects_nav(self):
        from job_scout.discovery.constants import looks_like_company

        assert looks_like_company("Acme Corp") is True
        assert looks_like_company("Careers") is False
        assert looks_like_company("") is False


class TestVCPortfolioSource:
    def test_extracts_companies(self, config):
        from job_scout.discovery.vc_portfolios import VCPortfolioSource

        html = """
        <html><body>
          <a href="/acme">Acme Corp</a>
          <a href="/beta">Beta Labs</a>
          <a href="/careers">Careers</a>
        </body></html>
        """
        source = VCPortfolioSource(config)
        with respx.mock:
            respx.get("https://www.blackbird.vc/portfolio").mock(
                return_value=httpx.Response(200, text=html)
            )
            companies = source.discover()

        names = {c.name for c in companies}
        assert "Acme Corp" in names
        assert "Beta Labs" in names
        assert "Careers" not in names
        for c in companies:
            assert "blackbird" in c.discovered_from


class TestFundingSource:
    def test_extracts_from_rss(self, config):
        from job_scout.discovery.funding import FundingSource

        rss = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item><title>Canva raises $50m</title></item>
          <item><title>Acme secures $10m funding round</title></item>
        </channel></rss>
        """
        source = FundingSource(config)
        with respx.mock:
            respx.get("https://www.startupdaily.net/feed/").mock(
                return_value=httpx.Response(200, text=rss)
            )
            companies = source.discover()

        names = {c.name for c in companies}
        assert "Canva" in names
        assert "Acme" in names
        for c in companies:
            assert "funding_news" in c.discovered_from
            assert "startupdaily" in c.discovered_from


class TestATSSearchSource:
    def test_parses_board_urls(self, config):
        from job_scout.discovery.ats_search import ATSSearchSource

        html = """
        <html><body>
          <a class="result__a" href="https://boards.greenhouse.io/canva">Canva</a>
          <a class="result__a" href="https://jobs.lever.co/atlassian">Atlassian</a>
          <a class="result__a" href="https://jobs.ashbyhq.com/linear">Linear</a>
        </body></html>
        """
        source = ATSSearchSource(config)
        with respx.mock:
            respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
                return_value=httpx.Response(200, text=html)
            )
            companies = source.discover()

        by_name = {c.name: c for c in companies}
        assert by_name["canva"].ats == ATSProvider.GREENHOUSE
        assert by_name["atlassian"].ats == ATSProvider.LEVER
        assert by_name["linear"].ats == ATSProvider.ASHBY
        assert all(c.discovered_from == ["ats_search"] for c in companies)

    def test_disabled_returns_empty(self, config):
        from job_scout.discovery.ats_search import ATSSearchSource

        config.ats_search_enabled = False
        source = ATSSearchSource(config)
        assert source.discover() == []

    def test_unwraps_ddg_redirect_urls(self, config):
        from job_scout.discovery.ats_search import ATSSearchSource

        html = """
        <html><body>
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fboards.greenhouse.io%2Fcanva">Canva</a>
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fjobs.lever.co%2Fatlassian">Atlassian</a>
        </body></html>
        """
        source = ATSSearchSource(config)
        with respx.mock:
            respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
                return_value=httpx.Response(200, text=html)
            )
            companies = source.discover()

        by_name = {c.name: c for c in companies}
        assert by_name["canva"].ats == ATSProvider.GREENHOUSE
        assert by_name["atlassian"].ats == ATSProvider.LEVER


class TestOrchestrator:
    def test_merge_dedups_and_unions_provenance(self, config, tmp_path):
        from job_scout.db import JobDB
        from job_scout.discovery import run_discovery
        from unittest.mock import patch

        db = JobDB(tmp_path / "t.db")

        class FakeSource:
            name = "fake"

            def __init__(self, cfg):
                self.config = cfg

            def discover(self):
                from job_scout.models import Company

                return [
                    Company(name="Canva", discovered_from=["funding_news"]),
                    Company(
                        name="Canva",
                        ats=ATSProvider.GREENHOUSE,
                        ats_slug="canva",
                        discovered_from=["ats_search"],
                    ),
                    Company(name="Atlassian", discovered_from=["vc_portfolios"]),
                ]

        with patch(
            "job_scout.discovery._build_sources", return_value=[FakeSource(config)]
        ):
            summary = run_discovery(config, db)

        db.close()
        assert summary["candidates"] == 2  # Canva merged into one
        assert summary["added"] == 2

    def test_disabled_noop(self, config, tmp_path):
        from job_scout.db import JobDB
        from job_scout.discovery import run_discovery

        config.enabled = False
        db = JobDB(tmp_path / "t.db")
        summary = run_discovery(config, db)
        db.close()
        assert summary["enabled"] is False
