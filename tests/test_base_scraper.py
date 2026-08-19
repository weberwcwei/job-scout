"""Tests for BaseScraper infrastructure: proxy rotation, max_pages."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
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


class TestHostGate:
    def test_spaces_requests_to_same_host(self):
        import time

        from job_scout.scrapers import _wait_host_gate

        host = "gate-a.example.com"
        _wait_host_gate(host, 0.3)
        t0 = time.monotonic()
        _wait_host_gate(host, 0.3)
        assert time.monotonic() - t0 >= 0.25

    def test_different_hosts_not_throttled(self):
        import time

        from job_scout.scrapers import _wait_host_gate

        _wait_host_gate("gate-b.example.com", 0.5)
        t0 = time.monotonic()
        _wait_host_gate("gate-c.example.com", 0.5)
        assert time.monotonic() - t0 < 0.2

    def test_interval_zero_skips_gate(self):
        import time

        from job_scout.scrapers import _wait_host_gate

        _wait_host_gate("gate-d.example.com", 0.0)
        t0 = time.monotonic()
        _wait_host_gate("gate-d.example.com", 0.0)
        assert time.monotonic() - t0 < 0.1

    def test_random_delay_happens_before_host_reservation(self):
        cfg = ScrapingConfig(
            max_retries=0,
            delay_min_seconds=0,
            delay_max_seconds=0,
            min_request_interval_seconds=1,
        )
        scraper = DummyScraper(cfg)
        calls = []

        class Client:
            def get(self, url):
                calls.append("get")
                return type("Response", (), {"status_code": 200})()

        client = Client()

        with (
            patch.object(scraper, "_delay", side_effect=lambda: calls.append("delay")),
            patch(
                "job_scout.scrapers._dispatch_host_request",
                side_effect=lambda host, interval, request: (
                    calls.append("dispatch"),
                    request(),
                )[1],
            ),
        ):
            scraper._get_with_retry(client, "https://example.com/jobs")

        assert calls == ["delay", "dispatch", "get"]

    def test_same_host_dispatch_is_serialized(self):
        from job_scout.scrapers import _dispatch_host_request

        first_started = Event()
        release_first = Event()
        second_started = Event()

        def first_request():
            first_started.set()
            release_first.wait(timeout=1)

        def second_request():
            second_started.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                _dispatch_host_request, "dispatch.example.com", 0.01, first_request
            )
            assert first_started.wait(timeout=1)
            second = pool.submit(
                _dispatch_host_request, "dispatch.example.com", 0.01, second_request
            )
            assert not second_started.wait(timeout=0.05)
            release_first.set()
            first.result(timeout=1)
            second.result(timeout=1)

        assert second_started.is_set()


class TestDescriptionCache:
    def test_concurrent_fetches_for_one_key_are_single_flight(self):
        from job_scout.scrapers import DescriptionCache

        cache = DescriptionCache()
        started = Event()
        release = Event()
        count_lock = Lock()
        fetch_count = 0

        def fetch():
            nonlocal fetch_count
            with count_lock:
                fetch_count += 1
            started.set()
            release.wait(timeout=1)
            return "description"

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(cache.get_or_fetch, ("linkedin", "123"), fetch)
            assert started.wait(timeout=1)
            second = pool.submit(cache.get_or_fetch, ("linkedin", "123"), fetch)
            release.set()
            assert first.result() == "description"
            assert second.result() == "description"

        assert fetch_count == 1


class TestRateLimitRetry:
    def test_retry_after_overrides_backoff(self):
        from unittest.mock import patch

        import httpx
        import respx

        cfg = ScrapingConfig(
            max_retries=1,
            delay_min_seconds=0,
            delay_max_seconds=0,
            min_request_interval_seconds=0,
        )
        scraper = DummyScraper(cfg)
        with respx.mock, patch("job_scout.scrapers.time.sleep") as sleep:
            respx.get("https://example.com/jobs").mock(
                side_effect=[
                    httpx.Response(429, headers={"Retry-After": "30"}),
                    httpx.Response(200, text="ok"),
                ]
            )
            with scraper._make_client() as client:
                resp = scraper._get_with_retry(client, "https://example.com/jobs")
        assert resp is not None and resp.status_code == 200
        sleep_waits = [c.args[0] for c in sleep.call_args_list if c.args]
        assert 30 in sleep_waits

    def test_gate_applied_across_workers(self):
        from unittest.mock import patch

        from job_scout.scrapers import _wait_host_gate

        with patch("job_scout.scrapers.time.sleep") as sleep:
            _wait_host_gate("gate-e.example.com", 1.0)
            _wait_host_gate("gate-e.example.com", 1.0)
        waits = [c.args[0] for c in sleep.call_args_list if c.args]
        assert waits and sum(waits) >= 0.9
