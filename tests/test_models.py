"""Tests for data models: Compensation, Location, Job."""

from __future__ import annotations


from job_scout.models import (
    Compensation,
    CompInterval,
    Job,
    Location,
    ScrapeParams,
    ScrapeRun,
    Site,
)


# --- Compensation.display (verbose) ---


class TestCompensationDisplay:
    def test_display_range_with_interval(self):
        comp = Compensation(
            min_amount=100000, max_amount=150000, interval=CompInterval.YEARLY
        )
        assert comp.display == "$100,000 - $150,000 /yearly"

    def test_display_single_value(self):
        comp = Compensation(min_amount=100000, max_amount=100000)
        assert comp.display == "$100,000"

    def test_display_no_min(self):
        comp = Compensation(min_amount=None)
        assert comp.display == ""

    def test_display_zero_min(self):
        comp = Compensation(min_amount=0)
        assert comp.display == ""

    def test_display_min_only_no_max(self):
        comp = Compensation(min_amount=75000, max_amount=None)
        assert comp.display == "$75,000"

    def test_display_hourly(self):
        comp = Compensation(min_amount=50, max_amount=75, interval=CompInterval.HOURLY)
        assert comp.display == "$50 - $75 /hourly"


# --- Location.display ---


class TestLocationDisplay:
    def test_city_and_state(self):
        loc = Location(city="San Francisco", state="CA")
        assert loc.display == "San Francisco, CA"

    def test_remote_flag(self):
        loc = Location(city="Anywhere", is_remote=True)
        assert "(Remote)" in loc.display

    def test_city_only(self):
        loc = Location(city="London", state=None)
        assert loc.display == "London"

    def test_no_city_no_state(self):
        loc = Location(city=None, state=None)
        assert loc.display == "Unknown"

    def test_remote_only(self):
        loc = Location(is_remote=True)
        assert loc.display == "(Remote)"

    def test_state_only(self):
        loc = Location(city=None, state="CA")
        assert loc.display == "CA"


# --- Job.dedup_key ---


class TestJobDedupKey:
    def test_dedup_key_deterministic(self):
        job = Job(
            source=Site.LINKEDIN,
            source_id="abc123",
            url="https://example.com",
            title="Test",
            company="Co",
        )
        key1 = job.dedup_key
        key2 = job.dedup_key
        assert key1 == key2

    def test_dedup_key_different_for_different_ids(self):
        job1 = Job(
            source=Site.LINKEDIN,
            source_id="abc",
            url="https://example.com",
            title="Test",
            company="Co",
        )
        job2 = Job(
            source=Site.LINKEDIN,
            source_id="def",
            url="https://example.com",
            title="Test",
            company="Co",
        )
        assert job1.dedup_key != job2.dedup_key

    def test_dedup_key_different_for_different_sources(self):
        job1 = Job(
            source=Site.LINKEDIN,
            source_id="abc",
            url="https://example.com",
            title="Test",
            company="Co",
        )
        job2 = Job(
            source=Site.INDEED,
            source_id="abc",
            url="https://example.com",
            title="Test",
            company="Co",
        )
        assert job1.dedup_key != job2.dedup_key

    def test_dedup_key_is_16_chars(self):
        job = Job(
            source=Site.GOOGLE,
            source_id="xyz",
            url="https://example.com",
            title="Test",
            company="Co",
        )
        assert len(job.dedup_key) == 16


# --- ScrapeParams defaults ---


class TestScrapeParams:
    def test_defaults(self):
        params = ScrapeParams(search_term="python", location="US")
        assert params.results_wanted == 25
        assert params.hours_old == 72
        assert params.distance_miles == 50


# --- ScrapeRun defaults ---


class TestScrapeRun:
    def test_defaults(self):
        run = ScrapeRun(site=Site.LINKEDIN, search_term="python", location="US")
        assert run.id is None
        assert run.jobs_found == 0
        assert run.jobs_new == 0
        assert run.error is None
        assert run.finished_at is None


# --- Compensation.display_concise ---


def test_display_concise_yearly_range():
    comp = Compensation(min_amount=181000, max_amount=318000)
    assert comp.display_concise == "$181k-$318k"


def test_display_concise_yearly_same():
    comp = Compensation(min_amount=181000, max_amount=181000)
    assert comp.display_concise == "$181k"


def test_display_concise_no_salary():
    comp = Compensation(min_amount=None)
    assert comp.display_concise == ""


def test_display_concise_zero():
    comp = Compensation(min_amount=0)
    assert comp.display_concise == ""


def test_display_concise_small_amount():
    comp = Compensation(min_amount=50, max_amount=75)
    assert comp.display_concise == "$50-$75"


def test_display_concise_min_only():
    comp = Compensation(min_amount=150000, max_amount=None)
    assert comp.display_concise == "$150k"


def test_display_concise_hourly_range():
    """Hourly rate (< 1000) uses raw dollar format."""
    comp = Compensation(min_amount=50, max_amount=75)
    assert comp.display_concise == "$50-$75"


def test_display_concise_max_only():
    """Max amount with no min returns empty (min_amount is falsy)."""
    comp = Compensation(min_amount=None, max_amount=200000)
    assert comp.display_concise == ""
