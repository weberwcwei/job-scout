"""Tests for M5: company and job scoring (kept separate)."""

from __future__ import annotations

from datetime import datetime, timedelta

from job_scout.models import ATSJob, Company, Location, Site


def _ats_job(**kw) -> ATSJob:
    defaults = dict(
        source=Site.GREENHOUSE,
        source_id="1",
        company_id=1,
        company="Canva",
        title="Senior Software Engineer",
        url="https://x",
        location=Location(city="Sydney", state="NSW", country="AU"),
        description="python backend aws role",
        salary_max=160000,
        posted_at=datetime.now() - timedelta(days=2),
        employment_type="full_time",
    )
    defaults.update(kw)
    return ATSJob(**defaults)


class TestCompanyScore:
    def test_staffing_agency_penalised(self):
        from job_scout.ranking.company_score import score_company

        c = Company(name="Hays Recruitment")
        score, breakdown = score_company(c, open_jobs=10)
        assert score == 0
        assert breakdown["staffing_penalty"] == 25

    def test_vc_backed_scores_high(self):
        from job_scout.ranking.company_score import score_company

        now = datetime.now()
        c = Company(
            name="Canva",
            discovered_from=["blackbird", "funding_news"],
            last_verified_at=now - timedelta(days=10),
        )
        score, breakdown = score_company(c, open_jobs=6)
        assert score > 30
        assert breakdown["vc"] == 15
        assert breakdown["hiring"] == 20

    def test_funding_decays(self):
        from job_scout.ranking.company_score import score_company

        fresh = Company(name="Co", last_verified_at=datetime.now())
        stale = Company(name="Co", last_verified_at=datetime.now() - timedelta(days=400))
        score_fresh, b_fresh = score_company(fresh)
        score_stale, b_stale = score_company(stale)
        assert score_fresh > score_stale
        assert b_fresh["funding"] > b_stale["funding"]


class TestJobScore:
    def test_strong_match_scores_high(self):
        from job_scout.ranking.job_score import score_job

        job = _ats_job()  # senior, Sydney NSW, python backend aws, 160k, fresh
        score, breakdown = score_job(job, company_score=80)
        assert score >= 60
        assert breakdown["location"] == 18
        assert breakdown["relevance"] >= 15
        assert breakdown["salary"] == 10
        assert breakdown["company"] == 8

    def test_remote_scores_location_full(self):
        from job_scout.ranking.job_score import score_job

        job = _ats_job(
            location=Location(is_remote=True, country="AU"),
            title="Junior Data Analyst",
            description="",
            salary_max=None,
        )
        score, breakdown = score_job(job, company_score=0)
        assert breakdown["location"] == 20
        assert score < 60

    def test_separate_from_company(self):
        """A great role at a low-scored company should still rank."""
        from job_scout.ranking.job_score import score_job

        job = _ats_job()
        low_score, _ = score_job(job, company_score=0)
        high_score, _ = score_job(job, company_score=90)
        assert high_score > low_score
        assert high_score - low_score <= 10  # company contributes at most 10