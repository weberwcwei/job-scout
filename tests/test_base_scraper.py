"""Tests for BaseScraper infrastructure: proxy rotation, max_pages."""

from __future__ import annotations

from unittest.mock import patch


from job_scout.config import ScrapingConfig
from job_scout.models import Job, ScrapeParams, Site
from job_scout.scrapers import BaseScraper


class DummyScraper(BaseScraper):
    """Concrete scraper for testing BaseScraper methods."""

    site = Site.LINKEDIN

    def scrape(self, params: ScrapeParams) -> list[Job]:
        return []


class TestProxyRotation:
    def test_round_robin_cycles(self):
        cfg = ScrapingConfig(proxies=["p1", "p2", "p3"])
        scraper = DummyScraper(cfg)
        results = [scraper._next_proxy() for _ in range(6)]
        assert results == ["p1", "p2", "p3", "p1", "p2", "p3"]

    def test_empty_proxies_returns_none(self):
        cfg = ScrapingConfig(proxies=[])
        scraper = DummyScraper(cfg)
        assert scraper._next_proxy() is None

    def test_single_proxy_always_same(self):
        cfg = ScrapingConfig(proxies=["only"])
        scraper = DummyScraper(cfg)
        results = [scraper._next_proxy() for _ in range(3)]
        assert results == ["only", "only", "only"]


class TestMakeClient:
    def test_make_client_without_tls(self):
        cfg = ScrapingConfig(use_tls_fingerprinting=False)
        scraper = DummyScraper(cfg)
        import httpx

        client = scraper._make_client()
        assert isinstance(client, httpx.Client)
        client.close()

    def test_make_client_tls_fallback_on_import_error(self):
        cfg = ScrapingConfig(use_tls_fingerprinting=True)
        scraper = DummyScraper(cfg)
        with patch("job_scout.scrapers.tls.create_tls_client", side_effect=ImportError):
            import httpx

            client = scraper._make_client()
            assert isinstance(client, httpx.Client)
            client.close()

    def test_make_client_with_proxy(self):
        cfg = ScrapingConfig(proxies=["http://proxy:8080"])
        scraper = DummyScraper(cfg)
        import httpx

        client = scraper._make_client()
        assert isinstance(client, httpx.Client)
        client.close()


class TestDedup:
    def test_is_dup_tracks_ids(self):
        cfg = ScrapingConfig()
        scraper = DummyScraper(cfg)
        assert scraper._is_dup("abc") is False
        assert scraper._is_dup("abc") is True
        assert scraper._is_dup("xyz") is False
