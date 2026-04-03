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
            respx.get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search").mock(
                return_value=httpx.Response(200, text="<html></html>")
            )
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
            respx.get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search").mock(
                return_value=httpx.Response(200, text=html)
            )
            respx.get(url__startswith="https://www.linkedin.com/jobs/view/").mock(
                return_value=httpx.Response(200, text="<div class='show-more-less-html__markup'>Description</div>")
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Software Engineer"
            assert jobs[0].company == "Acme Corp"
            assert jobs[0].source == Site.LINKEDIN


class TestIndeedScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from job_scout.scrapers.indeed import IndeedScraper

        scraper = IndeedScraper(scraping_config)
        with respx.mock:
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(200, json={
                    "data": {"jobSearch": {"results": [], "pageInfo": {"nextCursor": None}}}
                })
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_jobs(self, scraping_config, params):
        from job_scout.scrapers.indeed import IndeedScraper

        scraper = IndeedScraper(scraping_config)
        with respx.mock:
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(200, json={
                    "data": {"jobSearch": {
                        "results": [{
                            "job": {
                                "key": "abc123",
                                "title": "Backend Engineer",
                                "description": {"html": "<p>Great job</p>"},
                                "location": {"city": "NYC", "admin1Code": "NY", "countryCode": "US",
                                              "formatted": {"short": "NYC", "long": "NYC, NY"}},
                                "compensation": {},
                                "attributes": [],
                                "employer": {"name": "TechCo"},
                                "datePublished": 1711929600000,
                            }
                        }],
                        "pageInfo": {"nextCursor": None}
                    }}
                })
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Backend Engineer"
            assert jobs[0].company == "TechCo"
            assert jobs[0].source == Site.INDEED


class TestGlassdoorScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from job_scout.scrapers.glassdoor import GlassdoorScraper

        scraper = GlassdoorScraper(scraping_config)
        with respx.mock:
            respx.get("https://www.glassdoor.com").mock(
                return_value=httpx.Response(200, text="<html></html>")
            )
            respx.post("https://www.glassdoor.com/graph").mock(
                return_value=httpx.Response(200, json=[{
                    "data": {"jobListings": {"jobListings": [], "totalJobsCount": 0, "paginationCursors": []}}
                }])
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_listing(self, scraping_config, params):
        from job_scout.scrapers.glassdoor import GlassdoorScraper

        scraper = GlassdoorScraper(scraping_config)
        with respx.mock:
            respx.get("https://www.glassdoor.com").mock(
                return_value=httpx.Response(200, text="<html></html>")
            )
            respx.post("https://www.glassdoor.com/graph").mock(
                return_value=httpx.Response(200, json=[{
                    "data": {"jobListings": {
                        "jobListings": [{
                            "jobview": {
                                "header": {
                                    "jobLink": "/job/123",
                                    "jobTitleText": "Data Scientist",
                                    "employerNameFromSearch": "DataCo",
                                    "ageInDays": 2,
                                    "payPercentile10": 80000,
                                    "payPercentile90": 120000,
                                    "payCurrency": "USD",
                                    "payPeriod": "ANNUAL",
                                },
                                "job": {"listingId": "gd-999", "description": "ML role"},
                                "overview": {"name": "DataCo"},
                                "locationName": "Boston, MA",
                                "remoteWorkTypes": [],
                            }
                        }],
                        "totalJobsCount": 1,
                        "paginationCursors": [],
                    }}
                }])
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Data Scientist"
            assert jobs[0].source == Site.GLASSDOOR


class TestZipRecruiterScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from job_scout.scrapers.ziprecruiter import ZipRecruiterScraper

        scraper = ZipRecruiterScraper(scraping_config)
        with respx.mock:
            respx.get("https://api.ziprecruiter.com/jobs-app/jobs").mock(
                return_value=httpx.Response(200, json={"jobs": [], "continue_from": None})
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_jobs(self, scraping_config, params):
        from job_scout.scrapers.ziprecruiter import ZipRecruiterScraper

        scraper = ZipRecruiterScraper(scraping_config)
        with respx.mock:
            respx.get("https://api.ziprecruiter.com/jobs-app/jobs").mock(
                return_value=httpx.Response(200, json={
                    "jobs": [{
                        "id": "zr-001",
                        "name": "Frontend Dev",
                        "url": "https://ziprecruiter.com/j/zr-001",
                        "hiring_company": {"name": "WebCo"},
                        "job_city": "Austin",
                        "job_state": "TX",
                        "job_country": "US",
                        "snippet": "<b>React</b> developer needed",
                        "posted_time_friendly": "2 days ago",
                        "salary_min_annual": 90000,
                        "salary_max_annual": 130000,
                    }],
                    "continue_from": None,
                })
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Frontend Dev"
            assert jobs[0].source == Site.ZIPRECRUITER
            assert jobs[0].compensation is not None


class TestBaytScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from job_scout.scrapers.bayt import BaytScraper

        scraper = BaytScraper(scraping_config)
        with respx.mock:
            respx.get(url__startswith="https://www.bayt.com/en/international/jobs/").mock(
                return_value=httpx.Response(200, text="<html><body></body></html>")
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_cards(self, scraping_config, params):
        from job_scout.scrapers.bayt import BaytScraper

        html = """
        <html><body>
        <li data-js-job="1" data-job-id="bt-100">
            <h2><a href="/en/job/bt-100">DevOps Engineer</a></h2>
            <b class="company-name">CloudFirm</b>
            <span class="location-text">Dubai, UAE</span>
            <span class="date-posted">3 days ago</span>
        </li>
        </body></html>
        """
        scraper = BaytScraper(scraping_config)
        with respx.mock:
            respx.get(url__startswith="https://www.bayt.com/en/international/jobs/").mock(
                return_value=httpx.Response(200, text=html)
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "DevOps Engineer"
            assert jobs[0].source == Site.BAYT


class TestScraperRegistry:
    def test_all_sites_registered(self):
        from job_scout.scrapers import get_scraper

        cfg = ScrapingConfig()
        for site in ["linkedin", "indeed", "google", "glassdoor", "ziprecruiter", "bayt"]:
            scraper = get_scraper(site, cfg)
            assert scraper is not None

    def test_unknown_site_raises(self):
        from job_scout.scrapers import get_scraper

        with pytest.raises(ValueError, match="Unknown site"):
            get_scraper("fakeboard", ScrapingConfig())
