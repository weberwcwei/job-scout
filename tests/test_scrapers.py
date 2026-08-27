"""Tests for scraper implementations with mocked HTTP responses."""

from __future__ import annotations


import httpx
import pytest
import respx

from job_scout.config import ScrapingConfig
from job_scout.models import ScrapeParams, Site


@pytest.fixture
def scraping_config():
    return ScrapingConfig(
        delay_min_seconds=0,
        delay_max_seconds=0,
        max_retries=0,
        max_pages=2,
        min_request_interval_seconds=0,
    )


@pytest.fixture
def params():
    return ScrapeParams(
        search_term="software engineer",
        location="United States",
        results_wanted=10,
        hours_old=72,
    )


class TestLinkedInScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from job_scout.scrapers.linkedin import LinkedInScraper

        scraper = LinkedInScraper(scraping_config)
        with respx.mock:
            respx.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            ).mock(return_value=httpx.Response(200, text="<html></html>"))
            # Also mock description fetches
            respx.get("https://www.linkedin.com/jobs/view/").mock(
                return_value=httpx.Response(200, text="")
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_cards(self, scraping_config, params):
        from job_scout.scrapers.linkedin import LinkedInScraper

        html = """
        <html>
        <div class="base-search-card">
            <a class="base-card__full-link" href="https://linkedin.com/jobs/view/test-job-12345?refId=abc">Link</a>
            <span class="sr-only">Software Engineer</span>
            <h4 class="base-search-card__subtitle"><a>Acme Corp</a></h4>
            <div class="base-search-card__metadata">
                <span class="job-search-card__location">San Francisco, CA</span>
                <time class="job-search-card__listdate" datetime="2026-04-01"></time>
            </div>
        </div>
        </html>
        """
        scraper = LinkedInScraper(scraping_config)
        with respx.mock:
            respx.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            ).mock(return_value=httpx.Response(200, text=html))
            respx.get(url__startswith="https://www.linkedin.com/jobs/view/").mock(
                return_value=httpx.Response(
                    200,
                    text="<div class='show-more-less-html__markup'>Description</div>",
                )
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Software Engineer"
            assert jobs[0].company == "Acme Corp"
            assert jobs[0].source == Site.LINKEDIN

    def test_description_cache_shared_between_scrapers(self, scraping_config, params):
        from job_scout.scrapers.linkedin import LinkedInScraper

        html = """
        <html>
        <div class="base-search-card">
            <a class="base-card__full-link" href="https://linkedin.com/jobs/view/test-job-12345">Link</a>
            <span class="sr-only">Software Engineer</span>
            <h4 class="base-search-card__subtitle"><a>Acme Corp</a></h4>
            <div class="base-search-card__metadata">
                <span class="job-search-card__location">Sydney NSW</span>
            </div>
        </div>
        </html>
        """
        from job_scout.scrapers import DescriptionCache

        cache = DescriptionCache()
        s1 = LinkedInScraper(scraping_config)
        s2 = LinkedInScraper(scraping_config)
        s1.description_cache = cache
        s2.description_cache = cache
        with respx.mock:
            respx.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            ).mock(return_value=httpx.Response(200, text=html))
            route = respx.get(
                url__startswith="https://www.linkedin.com/jobs/view/"
            ).mock(
                return_value=httpx.Response(
                    200,
                    text="<div class='show-more-less-html__markup'>Description</div>",
                )
            )
            s1.scrape(params)
            s2.scrape(params)
        assert len(route.calls) == 1


class TestIndeedScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from job_scout.scrapers.indeed import IndeedScraper

        scraper = IndeedScraper(scraping_config)
        with respx.mock:
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "jobSearch": {
                                "results": [],
                                "pageInfo": {"nextCursor": None},
                            }
                        }
                    },
                )
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_jobs(self, scraping_config, params):
        from job_scout.scrapers.indeed import IndeedScraper

        scraper = IndeedScraper(scraping_config)
        with respx.mock:
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "jobSearch": {
                                "results": [
                                    {
                                        "job": {
                                            "key": "abc123",
                                            "title": "Backend Engineer",
                                            "description": {"html": "<p>Great job</p>"},
                                            "location": {
                                                "city": "NYC",
                                                "admin1Code": "NY",
                                                "countryCode": "US",
                                                "formatted": {
                                                    "short": "NYC",
                                                    "long": "NYC, NY",
                                                },
                                            },
                                            "compensation": {},
                                            "attributes": [],
                                            "employer": {"name": "TechCo"},
                                            "datePublished": 1711929600000,
                                        }
                                    }
                                ],
                                "pageInfo": {"nextCursor": None},
                            }
                        }
                    },
                )
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Backend Engineer"
            assert jobs[0].company == "TechCo"
            assert jobs[0].source == Site.INDEED

    def test_country_derived_from_location(self):
        from job_scout.scrapers.indeed import IndeedScraper

        scraper = IndeedScraper(ScrapingConfig())
        assert scraper._country_from_location("Remote Australia") == "AU"
        assert scraper._country_from_location("London UK") == "GB"
        assert scraper._country_from_location("Sydney NSW") is None
        assert scraper._country_from_location("Georgia") is None  # US state wins
        assert scraper._country_from_location(None) is None


class TestJoraScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from job_scout.scrapers.jora import JoraScraper

        scraper = JoraScraper(scraping_config)
        with respx.mock:
            respx.get(url__startswith="https://au.jora.com/j?").mock(
                return_value=httpx.Response(200, text="<html></html>")
            )
            respx.get(url__startswith="https://au.jora.com/job/").mock(
                return_value=httpx.Response(200, text="<html></html>")
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_cards(self, scraping_config, params):
        from job_scout.scrapers.jora import JoraScraper

        html = """
        <html><body>
        <article class="job-card" data-jd-payload='{"jobId":"job123","tk":"tok123"}'>
          <div class="job-title"><a class="job-link" href="/job/Software-Engineer-job123?x=1">Software Engineer</a></div>
          <div class="job-company">Acme Corp</div>
          <div class="job-location">Sydney NSW</div>
          <div class="job-listed-date">Posted 2d ago</div>
          <ul class="job-abstract"><li>Build cool stuff</li><li>Python required</li></ul>
          <div class="badges"><span class="badge"><span class="content">Permanent</span></span><span class="badge"><span class="content">$120,000 - $140,000 a year</span></span></div>
        </article>
        <article class="job-card" data-jd-payload='{"jobId":"job456","tk":"tok123"}'>
          <div class="job-title"><a class="job-link" href="/job/Senior-Engineer-job456">Senior Engineer</a></div>
          <div class="job-company">Beta Inc</div>
          <div class="job-location">Remote Australia</div>
          <div class="job-listed-date">Posted today</div>
          <ul class="job-abstract"><li>Lead the team</li></ul>
          <div class="badges"><span class="badge"><span class="content">Full time</span></span><span class="badge"><span class="content">$50 - $60 per hour</span></span></div>
        </article>
        </body></html>
        """
        scraper = JoraScraper(scraping_config)
        with respx.mock:
            respx.get(url__startswith="https://au.jora.com/j?").mock(
                return_value=httpx.Response(200, text=html)
            )
            detail = respx.get(url__startswith="https://au.jora.com/job/").mock(
                return_value=httpx.Response(
                    200,
                    text="<div id='job-description-container'>"
                    "Full python backend description text here</div>",
                )
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 2
            assert jobs[0].title == "Software Engineer"
            assert jobs[0].company == "Acme Corp"
            assert jobs[0].source == Site.JORA
            assert jobs[0].location.country == "AU"
            assert jobs[0].location.city == "Sydney"
            # TLS is off in this fixture: detail fetch is gated, abstract is used
            assert "python" not in jobs[0].description
            assert len(detail.calls) == 0
            assert jobs[0].compensation is not None
            assert jobs[0].compensation.currency == "AUD"
            assert jobs[0].compensation.interval.value == "yearly"
            assert jobs[0].date_posted is not None
            assert jobs[1].location.is_remote is True
            assert jobs[1].compensation.interval.value == "hourly"

    def test_fetch_description_parses_detail_page(self):
        from job_scout.scrapers.jora import JoraScraper

        class StubResp:
            status_code = 200
            text = (
                "<div id='job-description-container'>"
                "<p>Full python backend description text here</p></div>"
            )

        class StubClient:
            def get(self, url, **kwargs):
                return StubResp()

        scraper = JoraScraper(ScrapingConfig())
        from job_scout.scrapers import DescriptionCache

        scraper.description_cache = DescriptionCache()
        desc = scraper._fetch_description(StubClient(), "/job/X-job123", "job123")
        assert "python" in desc
        assert scraper.description_cache[("jora", "job123")] == desc

    def test_filters_cards_older_than_requested_window(self, scraping_config):
        from job_scout.scrapers.jora import JoraScraper

        html = """
        <article class="job-card">
          <div class="job-title"><a href="/job/Old-job-old1">Old job</a></div>
          <div class="job-listed-date">Posted 10d ago</div>
        </article>
        <article class="job-card">
          <div class="job-title"><a href="/job/New-job-new1">New job</a></div>
          <div class="job-listed-date">Posted today</div>
        </article>
        """
        params = ScrapeParams(
            search_term="engineer",
            location="Australia",
            results_wanted=10,
            hours_old=72,
            country="AU",
        )
        scraper = JoraScraper(scraping_config)
        with respx.mock:
            respx.get(url__startswith="https://au.jora.com/j?").mock(
                return_value=httpx.Response(200, text=html)
            )
            jobs = scraper.scrape(params)

        assert [job.title for job in jobs] == ["New job"]


class TestScraperRegistry:
    def test_all_sites_registered(self):
        from job_scout.scrapers import get_scraper

        cfg = ScrapingConfig()
        for site in [
            "linkedin",
            "indeed",
            "jora",
        ]:
            scraper = get_scraper(site, cfg)
            assert scraper is not None

    def test_unknown_site_raises(self):
        from job_scout.scrapers import get_scraper

        with pytest.raises(ValueError, match="Unknown site"):
            get_scraper("fakeboard", ScrapingConfig())
