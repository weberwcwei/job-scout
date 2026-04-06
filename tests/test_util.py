"""Tests for util.py — HTML processing, email extraction, job type extraction, etc."""

from __future__ import annotations


from job_scout.models import CompInterval, JobType
from job_scout.util import (
    currency_parser,
    extract_emails,
    extract_job_types,
    html_to_text,
    is_remote,
    parse_compensation_interval,
)


# --- html_to_text ---


class TestHtmlToText:
    def test_basic_html(self):
        assert html_to_text("<p>Hello <b>World</b></p>") == "Hello World"

    def test_strips_tags(self):
        result = html_to_text("<div><span>foo</span><br/><span>bar</span></div>")
        assert "foo" in result
        assert "bar" in result
        assert "<" not in result

    def test_collapses_whitespace(self):
        result = html_to_text("<p>lots   of    spaces</p>")
        assert "  " not in result

    def test_none_input(self):
        assert html_to_text(None) == ""

    def test_empty_string(self):
        assert html_to_text("") == ""

    def test_plain_text_passthrough(self):
        assert html_to_text("no html here") == "no html here"


# --- extract_emails ---


class TestExtractEmails:
    def test_single_email(self):
        assert extract_emails("Contact us at hr@example.com") == ["hr@example.com"]

    def test_multiple_emails(self):
        result = extract_emails("a@b.com and c@d.org")
        assert "a@b.com" in result
        assert "c@d.org" in result

    def test_no_emails(self):
        assert extract_emails("no emails here") == []

    def test_none_input(self):
        assert extract_emails(None) == []

    def test_empty_string(self):
        assert extract_emails("") == []

    def test_email_with_plus(self):
        result = extract_emails("user+tag@example.com")
        assert result == ["user+tag@example.com"]

    def test_email_with_dots(self):
        result = extract_emails("first.last@company.co.uk")
        assert result == ["first.last@company.co.uk"]


# --- extract_job_types ---


class TestExtractJobTypes:
    def test_full_time(self):
        result = extract_job_types("This is a Full Time position")
        assert JobType.FULL_TIME in result

    def test_part_time(self):
        result = extract_job_types("Part time hours")
        assert JobType.PART_TIME in result

    def test_internship(self):
        result = extract_job_types("Summer internship program")
        assert JobType.INTERNSHIP in result

    def test_contract(self):
        result = extract_job_types("Contract role, 6 months")
        assert JobType.CONTRACT in result

    def test_multiple_types(self):
        result = extract_job_types("Full time or contract available")
        assert JobType.FULL_TIME in result
        assert JobType.CONTRACT in result

    def test_no_match(self):
        assert extract_job_types("great opportunity") == []

    def test_none_input(self):
        assert extract_job_types(None) == []

    def test_empty_string(self):
        assert extract_job_types("") == []

    def test_case_insensitive(self):
        result = extract_job_types("FULL TIME")
        assert JobType.FULL_TIME in result

    def test_fulltime_no_space(self):
        result = extract_job_types("fulltime")
        assert JobType.FULL_TIME in result


# --- currency_parser ---


class TestCurrencyParser:
    def test_simple_number(self):
        assert currency_parser("100000") == 100000.0

    def test_with_dollar_sign(self):
        assert currency_parser("$100,000") == 100000.0

    def test_with_decimal(self):
        assert currency_parser("$50.00") == 50.00

    def test_european_format(self):
        """European format: 1.000,50 -> 1000.50"""
        assert currency_parser("1.000,50") == 1000.50

    def test_negative_number(self):
        assert currency_parser("-500") == -500.0


# --- parse_compensation_interval ---


class TestParseCompensationInterval:
    def test_yearly(self):
        assert parse_compensation_interval("YEAR") == CompInterval.YEARLY

    def test_hourly(self):
        assert parse_compensation_interval("HOUR") == CompInterval.HOURLY

    def test_monthly(self):
        assert parse_compensation_interval("MONTH") == CompInterval.MONTHLY

    def test_weekly(self):
        assert parse_compensation_interval("WEEK") == CompInterval.WEEKLY

    def test_daily(self):
        assert parse_compensation_interval("DAY") == CompInterval.DAILY

    def test_case_insensitive(self):
        assert parse_compensation_interval("year") == CompInterval.YEARLY

    def test_unknown_returns_none(self):
        assert parse_compensation_interval("biweekly") is None


# --- is_remote ---


class TestIsRemote:
    def test_remote_in_title(self):
        assert is_remote("Remote Software Engineer", "") is True

    def test_remote_in_description(self):
        assert is_remote("Engineer", "This is a remote position") is True

    def test_wfh_in_description(self):
        assert is_remote("Engineer", "Work from home available") is True

    def test_remote_in_location(self):
        assert is_remote("Engineer", "", "Remote, US") is True

    def test_not_remote(self):
        assert is_remote("Engineer", "On-site in NYC", "New York, NY") is False

    def test_case_insensitive(self):
        assert is_remote("REMOTE Engineer", "") is True

    def test_empty_strings(self):
        assert is_remote("", "", "") is False
