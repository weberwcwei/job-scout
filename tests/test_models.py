"""Tests for data models: Compensation, Location, Job."""

from __future__ import annotations


import pytest

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


# --- Location normalization ---


class TestLocationNormalization:
    """Tests for Location model_validator normalization rules."""

    # Rule 1: Whitespace stripping
    def test_strip_whitespace_city(self):
        loc = Location(city="  Detroit  ")
        assert loc.city == "Detroit"

    def test_strip_whitespace_state(self):
        loc = Location(state="  CA  ")
        assert loc.state == "CA"

    def test_strip_whitespace_country(self):
        loc = Location(country="  US  ")
        assert loc.country == "US"

    def test_empty_string_becomes_none(self):
        loc = Location(city="", state="", country="")
        assert loc.city is None
        assert loc.state is None
        assert loc.country is None

    def test_whitespace_only_becomes_none(self):
        loc = Location(city="   ", state="  ")
        assert loc.city is None
        assert loc.state is None

    # Rule 2: Remote city clearing
    def test_remote_city_with_flag(self):
        loc = Location(city="Remote", is_remote=True)
        assert loc.city is None
        assert loc.is_remote is True

    def test_remote_city_case_insensitive(self):
        loc = Location(city="REMOTE", is_remote=True)
        assert loc.city is None

    def test_remote_city_without_flag_stays(self):
        loc = Location(city="Remote", is_remote=False)
        assert loc.city == "Remote"

    def test_remote_substring_not_cleared(self):
        loc = Location(city="Remote Area", is_remote=True)
        assert loc.city == "Remote Area"

    # Rule 3: Country in city field (state must be None)
    def test_country_name_in_city(self):
        loc = Location(city="United States", state=None)
        assert loc.city is None
        assert loc.country == "US"

    def test_country_in_city_case_insensitive(self):
        loc = Location(city="united kingdom", state=None)
        assert loc.city is None
        assert loc.country == "GB"

    def test_country_in_city_not_triggered_when_state_set(self):
        """Rule 3 only fires when state is None."""
        loc = Location(city="Canada", state="ON")
        assert loc.city == "Canada"
        assert loc.state == "ON"

    def test_usa_variant_in_city(self):
        loc = Location(city="USA", state=None)
        assert loc.city is None
        assert loc.country == "US"

    # Rule 4: Shifted fields (city=state_name, state=country_name)
    def test_shifted_california_us(self):
        loc = Location(city="California", state="United States")
        assert loc.city is None
        assert loc.state == "CA"
        assert loc.country == "US"

    def test_shifted_new_york_us(self):
        loc = Location(city="New York", state="United States")
        assert loc.city is None
        assert loc.state == "NY"
        assert loc.country == "US"

    def test_shifted_case_insensitive(self):
        loc = Location(city="texas", state="united States")
        assert loc.city is None
        assert loc.state == "TX"
        assert loc.country == "US"

    def test_state_in_city_but_state_not_country(self):
        """city='California', state='CA' -- rule 4 must NOT fire."""
        loc = Location(city="California", state="CA")
        assert loc.city == "California"
        assert loc.state == "CA"

    # Rule 5: Country in state field
    def test_country_in_state(self):
        loc = Location(city="Detroit", state="United States")
        assert loc.city == "Detroit"
        assert loc.state is None
        assert loc.country == "US"

    def test_country_in_state_no_city(self):
        loc = Location(city=None, state="United States")
        assert loc.state is None
        assert loc.country == "US"

    def test_country_in_state_canada(self):
        loc = Location(city="Toronto", state="Canada")
        assert loc.city == "Toronto"
        assert loc.state is None
        assert loc.country == "CA"

    def test_georgia_in_state_stays_as_state(self):
        """Georgia is both a US state and country -- US state takes precedence in state field."""
        loc = Location(city="Atlanta", state="Georgia")
        assert loc.state == "GA"
        assert loc.country == "US"

    # Rule 6: State normalization
    def test_state_full_name_to_abbrev(self):
        loc = Location(state="California")
        assert loc.state == "CA"

    def test_state_multi_word(self):
        loc = Location(state="New York")
        assert loc.state == "NY"

    def test_state_dc(self):
        loc = Location(state="District of Columbia")
        assert loc.state == "DC"

    def test_state_already_abbreviated(self):
        loc = Location(state="CA")
        assert loc.state == "CA"

    def test_state_lowercase_code_uppercased(self):
        loc = Location(state="ca")
        assert loc.state == "CA"

    def test_state_unknown_passthrough(self):
        loc = Location(state="Ontario")
        assert loc.state == "Ontario"

    # Rule 7: Country normalization
    def test_country_full_name(self):
        loc = Location(country="United States")
        assert loc.country == "US"

    def test_country_long_variant(self):
        loc = Location(country="United States of America")
        assert loc.country == "US"

    def test_country_uk_to_gb(self):
        loc = Location(country="United Kingdom")
        assert loc.country == "GB"

    def test_country_already_code(self):
        loc = Location(country="US")
        assert loc.country == "US"

    def test_country_lowercase_code_uppercased(self):
        loc = Location(country="us")
        assert loc.country == "US"

    def test_country_unknown_passthrough(self):
        loc = Location(country="Atlantis")
        assert loc.country == "Atlantis"

    # Multi-rule interactions
    def test_shifted_with_whitespace(self):
        loc = Location(city="  California  ", state="  United States  ")
        assert loc.city is None
        assert loc.state == "CA"
        assert loc.country == "US"

    def test_detroit_country_in_state(self):
        """Detroit is NOT a state name, so rule 4 skipped; rule 5 moves country from state."""
        loc = Location(city="Detroit", state="United States")
        assert loc.city == "Detroit"
        assert loc.state is None
        assert loc.country == "US"

    def test_full_normalization_pipeline(self):
        loc = Location(city="Detroit", state="Michigan", country="United States")
        assert loc.city == "Detroit"
        assert loc.state == "MI"
        assert loc.country == "US"

    def test_remote_plus_country_in_state(self):
        loc = Location(city="Remote", state="United States", is_remote=True)
        assert loc.city is None
        assert loc.state is None
        assert loc.country == "US"
        assert loc.is_remote is True

    # Pass-through: valid data unchanged
    def test_valid_location_unchanged(self):
        loc = Location(city="San Francisco", state="CA", country="US")
        assert loc.city == "San Francisco"
        assert loc.state == "CA"
        assert loc.country == "US"

    def test_metro_area_unchanged(self):
        loc = Location(city="San Francisco Bay Area", state="CA")
        assert loc.city == "San Francisco Bay Area"
        assert loc.state == "CA"

    def test_international_unchanged(self):
        loc = Location(city="London", state=None, country="GB")
        assert loc.city == "London"
        assert loc.country == "GB"

    def test_default_construction(self):
        loc = Location()
        assert loc.city is None
        assert loc.state is None
        assert loc.country == "US"
        assert loc.is_remote is False

    def test_all_none(self):
        loc = Location(city=None, state=None, country=None)
        assert loc.city is None
        assert loc.state is None
        assert loc.country is None

    # Rule ordering: rule 4 must fire before rules 3 and 5
    def test_rule4_before_rule5(self):
        """If rule 5 ran first, state='United States' -> country, leaving city='California' orphaned."""
        loc = Location(city="California", state="United States")
        assert loc.city is None
        assert loc.state == "CA"
        assert loc.country == "US"

    def test_georgia_ambiguity_with_country_state(self):
        """city='Georgia', state='United States' -> rule 4 treats Georgia as US state."""
        loc = Location(city="Georgia", state="United States")
        assert loc.city is None
        assert loc.state == "GA"
        assert loc.country == "US"

    def test_georgia_ambiguity_no_state(self):
        """city='Georgia', state=None -> rule 3 treats as country Georgia."""
        loc = Location(city="Georgia", state=None)
        assert loc.city is None
        assert loc.country == "GE"


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (
            {"city": "United States"},
            {"city": None, "state": None, "country": "US"},
        ),
        (
            {"city": "California", "state": "United States"},
            {"city": None, "state": "CA", "country": "US"},
        ),
        (
            {"city": "Detroit", "state": "United States"},
            {"city": "Detroit", "state": None, "country": "US"},
        ),
        (
            {"city": "Los Angeles", "state": "California"},
            {"city": "Los Angeles", "state": "CA", "country": "US"},
        ),
        (
            {"city": "Remote", "is_remote": True},
            {"city": None, "state": None, "country": "US"},
        ),
    ],
    ids=[
        "country-in-city",
        "shifted-california-us",
        "detroit-country-in-state",
        "state-full-name",
        "remote-city-with-flag",
    ],
)
def test_real_data_normalization(kwargs, expected):
    """Test against the actual data issues found in the 3,184-job database."""
    loc = Location(**kwargs)
    for key, value in expected.items():
        assert getattr(loc, key) == value, (
            f"{key}: expected {value!r}, got {getattr(loc, key)!r}"
        )


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
