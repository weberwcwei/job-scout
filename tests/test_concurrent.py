"""Tests for concurrent scraping behavior."""

from __future__ import annotations

import time


from job_scout.config import ScrapingConfig
from job_scout.models import Job, Location, ScrapeParams, Site
from job_scout.scrapers import BaseScraper


def _make_job(source: Site, source_id: str, title: str = "Test Job") -> Job:
    return Job(
        source=source,
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=title,
        company="TestCo",
        location=Location(),
    )


class SlowScraper(BaseScraper):
    """Scraper that sleeps to test concurrency."""

    site = Site.LINKEDIN

    def __init__(self, config, delay=0.1, jobs=None):
        super().__init__(config)
        self._delay_time = delay
        self._jobs = jobs or []

    def scrape(self, params: ScrapeParams) -> list[Job]:
        time.sleep(self._delay_time)
        return self._jobs


class ErrorScraper(BaseScraper):
    """Scraper that raises an exception."""

    site = Site.INDEED

    def scrape(self, params: ScrapeParams) -> list[Job]:
        raise RuntimeError("Scraper failed")


class TestConcurrentExecution:
    def test_parallel_faster_than_sequential(self):
        """3 scrapers each sleeping 0.1s should finish in ~0.1s with 3 workers, not 0.3s."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        cfg = ScrapingConfig(delay_min_seconds=0, delay_max_seconds=0)

        def worker(i):
            scraper = SlowScraper(cfg, delay=0.1)
            return scraper.scrape(ScrapeParams(
                search_term="test", location="US", results_wanted=5
            ))

        start = time.time()
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(worker, i) for i in range(3)]
            for f in as_completed(futures):
                f.result()
        elapsed = time.time() - start

        assert elapsed < 0.25  # Should be ~0.1s, not 0.3s

    def test_error_isolation(self):
        """One failing scraper should not prevent others from completing."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        cfg = ScrapingConfig(delay_min_seconds=0, delay_max_seconds=0)
        params = ScrapeParams(search_term="test", location="US", results_wanted=5)

        def run_scraper(scraper):
            try:
                jobs = scraper.scrape(params)
                return jobs, None
            except Exception as e:
                return [], str(e)

        scrapers = [
            SlowScraper(cfg, delay=0, jobs=[_make_job(Site.LINKEDIN, "1")]),
            ErrorScraper(cfg),
            SlowScraper(cfg, delay=0, jobs=[_make_job(Site.GOOGLE, "2")]),
        ]

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(run_scraper, s) for s in scrapers]
            results = [f.result() for f in as_completed(futures)]

        successful = [r for r in results if r[1] is None]
        failed = [r for r in results if r[1] is not None]

        assert len(successful) == 2
        assert len(failed) == 1
        assert "Scraper failed" in failed[0][1]
