"""Tests for scorer.py — JobScorer scoring engine."""

from __future__ import annotations

from datetime import date, timedelta


from job_scout.config import (
    CompanyTiers,
    DealbreakersConfig,
    KeywordConfig,
    ProfileConfig,
    TitleSignal,
)
from job_scout.models import Job, Location, Site
from job_scout.scorer import JobScorer


def _profile(**overrides) -> ProfileConfig:
    defaults = {
        "name": "Test",
        "target_title": "Software Engineer",
        "keywords": KeywordConfig(
            critical=["python", "backend"],
            strong=["distributed", "microservices"],
            moderate=["aws", "docker"],
            weak=["linux"],
        ),
        "target_companies": CompanyTiers(
            tier1=["Google", "Meta"],
            tier2=["Stripe", "Datadog"],
            tier3=["Twilio"],
        ),
        "title_signals": [
            TitleSignal(pattern="software engineer", points=15),
            TitleSignal(pattern="backend", points=12),
            TitleSignal(pattern="senior", points=5),
        ],
        "dealbreakers": DealbreakersConfig(
            title_patterns=[r"(?i)\bintern\b", r"(?i)\bjunior\b"],
            company_patterns=[r"(?i)staffing"],
            description_patterns=[r"(?i)must be on-?site"],
        ),
    }
    defaults.update(overrides)
    return ProfileConfig(**defaults)


def _job(
    *,
    title="Software Engineer",
    company="Acme Corp",
    description="python backend distributed systems",
    date_posted=None,
    **kwargs,
) -> Job:
    return Job(
        source=Site.LINKEDIN,
        source_id="test-1",
        url="https://example.com/1",
        title=title,
        company=company,
        location=Location(city="SF", state="CA"),
        description=description,
        date_posted=date_posted,
        **kwargs,
    )


# --- Dealbreaker tests ---


class TestDealbreakers:
    def test_title_dealbreaker_zeros_score(self):
        scorer = JobScorer(_profile())
        total, breakdown = scorer.score(_job(title="Software Intern"))
        assert total == 0
        assert breakdown.get("dealbreaker") is True

    def test_company_dealbreaker_zeros_score(self):
        scorer = JobScorer(_profile())
        total, breakdown = scorer.score(_job(company="ABC Staffing Agency"))
        assert total == 0
        assert breakdown.get("dealbreaker") is True

    def test_description_dealbreaker_zeros_score(self):
        scorer = JobScorer(_profile())
        total, breakdown = scorer.score(
            _job(description="Great role. Must be on-site 5 days/week.")
        )
        assert total == 0
        assert breakdown.get("dealbreaker") is True

    def test_no_dealbreaker_returns_positive_score(self):
        scorer = JobScorer(_profile())
        total, breakdown = scorer.score(_job())
        assert total > 0
        assert "dealbreaker" not in breakdown

    def test_empty_description_skips_desc_dealbreaker(self):
        scorer = JobScorer(_profile())
        total, _ = scorer.score(_job(description=""))
        assert total >= 0  # Should not crash or trigger dealbreaker

    def test_junior_in_title_triggers_dealbreaker(self):
        scorer = JobScorer(_profile())
        total, breakdown = scorer.score(_job(title="Junior Developer"))
        assert total == 0
        assert breakdown.get("dealbreaker") is True


# --- Keyword scoring tests ---


class TestKeywordScoring:
    def test_critical_keywords_contribute(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(description="python backend"))
        assert breakdown["keyword"] > 0

    def test_no_critical_caps_at_10(self):
        """Without critical keyword hits, keyword score is capped at 10."""
        scorer = JobScorer(_profile())
        # Description has only moderate/weak keywords, no critical
        _, breakdown = scorer.score(_job(description="aws docker linux linux linux"))
        assert breakdown["keyword"] <= 10

    def test_strong_only_without_critical_gives_zero(self):
        """Strong keywords alone (no critical) give 0 in the capped path."""
        scorer = JobScorer(_profile())
        # Only strong keywords, no critical, no moderate/weak
        _, breakdown = scorer.score(_job(description="distributed microservices"))
        # min(10, moderate_hits*2 + weak_hits) = min(10, 0) = 0
        assert breakdown["keyword"] == 0

    def test_all_tiers_hit_maximizes_score(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(
            _job(
                description="python backend distributed microservices aws docker linux"
            )
        )
        # Should be at or near max 55
        assert breakdown["keyword"] >= 20

    def test_empty_keywords_config(self):
        """With no keywords defined, keyword score should be 0."""
        profile = _profile(keywords=KeywordConfig())
        scorer = JobScorer(profile)
        _, breakdown = scorer.score(_job(description="anything here"))
        assert breakdown["keyword"] == 0

    def test_keyword_matching_is_case_insensitive(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(description="PYTHON BACKEND"))
        assert breakdown["keyword"] > 0

    def test_keyword_in_title_also_counts(self):
        scorer = JobScorer(_profile())
        _, b1 = scorer.score(_job(title="Python Backend Engineer", description=""))
        assert b1["keyword"] > 0


# --- Company scoring tests ---


class TestCompanyScoring:
    def test_tier1_gives_15(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(company="Google LLC"))
        assert breakdown["company"] == 15

    def test_tier2_gives_10(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(company="Stripe Inc"))
        assert breakdown["company"] == 10

    def test_tier3_gives_6(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(company="Twilio"))
        assert breakdown["company"] == 6

    def test_unknown_company_gives_0(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(company="Random Startup"))
        assert breakdown["company"] == 0

    def test_company_matching_is_case_insensitive(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(company="google"))
        assert breakdown["company"] == 15

    def test_company_substring_match(self):
        """'Meta' should match 'Meta Platforms Inc'."""
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(company="Meta Platforms Inc"))
        assert breakdown["company"] == 15


# --- Title scoring tests ---


class TestTitleScoring:
    def test_matching_title_gives_points(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(title="Software Engineer"))
        assert breakdown["title"] == 15

    def test_best_match_wins(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(title="Senior Software Engineer"))
        # "software engineer" = 15, "senior" = 5; best = 15
        assert breakdown["title"] == 15

    def test_no_title_match_gives_0(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(title="Product Manager"))
        assert breakdown["title"] == 0

    def test_title_capped_at_20(self):
        profile = _profile(title_signals=[TitleSignal(pattern="engineer", points=25)])
        scorer = JobScorer(profile)
        _, breakdown = scorer.score(_job(title="Engineer"))
        assert breakdown["title"] == 20


# --- Recency scoring tests ---


class TestRecencyScoring:
    def test_no_date_gives_3(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(date_posted=None))
        assert breakdown["recency"] == 3

    def test_today_gives_10(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(date_posted=date.today()))
        assert breakdown["recency"] == 10

    def test_yesterday_gives_10(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(date_posted=date.today() - timedelta(days=1)))
        assert breakdown["recency"] == 10

    def test_3_days_old_gives_8(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(date_posted=date.today() - timedelta(days=3)))
        assert breakdown["recency"] == 8

    def test_7_days_old_gives_5(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(date_posted=date.today() - timedelta(days=7)))
        assert breakdown["recency"] == 5

    def test_14_days_old_gives_3(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(date_posted=date.today() - timedelta(days=14)))
        assert breakdown["recency"] == 3

    def test_old_job_gives_1(self):
        scorer = JobScorer(_profile())
        _, breakdown = scorer.score(_job(date_posted=date.today() - timedelta(days=30)))
        assert breakdown["recency"] == 1


# --- Total score tests ---


class TestTotalScore:
    def test_score_clamped_to_0_100(self):
        scorer = JobScorer(_profile())
        total, _ = scorer.score(_job())
        assert 0 <= total <= 100

    def test_high_score_job(self):
        """A job matching everything should score high."""
        scorer = JobScorer(_profile())
        total, breakdown = scorer.score(
            _job(
                title="Senior Software Engineer",
                company="Google",
                description="python backend distributed microservices aws docker linux",
                date_posted=date.today(),
            )
        )
        assert total >= 50
        assert breakdown["keyword"] > 0
        assert breakdown["company"] == 15
        assert breakdown["title"] > 0
        assert breakdown["recency"] == 10

    def test_low_score_job(self):
        """A job matching nothing should score very low."""
        scorer = JobScorer(_profile())
        total, _ = scorer.score(
            _job(
                title="Product Manager",
                company="Unknown Co",
                description="manage product roadmap stakeholders",
                date_posted=date.today() - timedelta(days=30),
            )
        )
        assert total <= 15
