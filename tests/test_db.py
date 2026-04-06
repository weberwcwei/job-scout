"""Tests for db.py — batch_update_scores and get_jobs filter extensions."""

from __future__ import annotations

import pytest

from job_scout.db import JobDB
from job_scout.models import Job, Location, Site


def _make_job(
    source_id: str, *, source: str = "linkedin", score: int = 50, status: str = "new"
) -> Job:
    return Job(
        source=Site(source),
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title="Software Engineer",
        company="TestCo",
        location=Location(city="SF", state="CA"),
        description="A job",
        score=score,
        score_breakdown={"keyword": score},
        status=status,
    )


@pytest.fixture()
def db(tmp_path):
    db = JobDB(tmp_path / "test.db")
    yield db
    db.close()


class TestBatchUpdateScores:
    def test_updates_all_scores(self, db):
        _, id1 = db.upsert_job(_make_job("a1", score=10))
        _, id2 = db.upsert_job(_make_job("a2", score=20))

        db.batch_update_scores(
            [
                (id1, 80, {"keyword": 50, "company": 15, "title": 10, "recency": 5}),
                (id2, 60, {"keyword": 30, "company": 15, "title": 10, "recency": 5}),
            ]
        )

        job1 = db.get_job(id1)
        job2 = db.get_job(id2)
        assert job1.score == 80
        assert job2.score == 60
        assert job1.score_breakdown["keyword"] == 50

    def test_rollback_on_failure(self, db):
        _, id1 = db.upsert_job(_make_job("b1", score=10))

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            db.batch_update_scores(
                [
                    (id1, 80, {"keyword": 80}),
                    (id1, 90, {"bad": Unserializable()}),  # json.dumps will fail
                ]
            )

        job = db.get_job(id1)
        assert job.score == 10  # unchanged — transaction rolled back


class TestGetJobsSourceFilter:
    def test_filters_by_source(self, db):
        db.upsert_job(_make_job("s1", source="linkedin"))
        db.upsert_job(_make_job("s2", source="indeed"))
        db.upsert_job(_make_job("s3", source="linkedin"))

        linkedin_jobs = db.get_jobs(source="linkedin")
        assert len(linkedin_jobs) == 2
        assert all(j.source == Site.LINKEDIN for j in linkedin_jobs)

    def test_source_filter_returns_empty_for_no_match(self, db):
        db.upsert_job(_make_job("s4", source="linkedin"))
        jobs = db.get_jobs(source="indeed")
        assert jobs == []


class TestGetJobsLimitNone:
    def test_returns_all_when_limit_none(self, db):
        for i in range(60):
            db.upsert_job(_make_job(f"l{i}"))

        # Default limit=50 should return 50
        limited = db.get_jobs()
        assert len(limited) == 50

        # limit=None returns all
        all_jobs = db.get_jobs(limit=None)
        assert len(all_jobs) == 60


class TestGetStatsZeroResultRuns:
    def test_counts_zero_result_runs(self, db):
        """get_stats() returns count of non-error runs with jobs_found=0."""
        from job_scout.models import ScrapeRun, Site
        from datetime import datetime

        # A zero-result run (no error)
        run = ScrapeRun(
            site=Site.LINKEDIN,
            search_term="python",
            location="Remote",
            started_at=datetime.now(),
        )
        run_id = db.record_run(run)
        db.finish_run(run_id, jobs_found=0, jobs_new=0)

        # A successful run with results
        run2 = ScrapeRun(
            site=Site.INDEED,
            search_term="python",
            location="Remote",
            started_at=datetime.now(),
        )
        run_id2 = db.record_run(run2)
        db.finish_run(run_id2, jobs_found=5, jobs_new=3)

        # An error run (should NOT count as zero-result)
        run3 = ScrapeRun(
            site=Site.GOOGLE,
            search_term="python",
            location="Remote",
            started_at=datetime.now(),
        )
        run_id3 = db.record_run(run3)
        db.finish_run(run_id3, jobs_found=0, jobs_new=0, error="timeout")

        stats = db.get_stats()
        assert stats["zero_result_runs"]["count"] == 1
        assert len(stats["zero_result_runs"]["recent"]) == 1
        assert stats["zero_result_runs"]["recent"][0]["site"] == "linkedin"

    def test_zero_result_runs_empty_when_none(self, db):
        """get_stats() returns zero count when all runs had results."""
        from job_scout.models import ScrapeRun, Site
        from datetime import datetime

        run = ScrapeRun(
            site=Site.LINKEDIN,
            search_term="python",
            location="Remote",
            started_at=datetime.now(),
        )
        run_id = db.record_run(run)
        db.finish_run(run_id, jobs_found=10, jobs_new=5)

        stats = db.get_stats()
        assert stats["zero_result_runs"]["count"] == 0
        assert stats["zero_result_runs"]["recent"] == []


class TestGetJobsSinceFilter:
    def test_since_filters_by_date_scraped(self, db):
        """get_jobs(since=cutoff) returns only jobs scraped after cutoff."""
        from datetime import datetime, timedelta

        old_job = _make_job("old1")
        old_job.date_scraped = datetime.now() - timedelta(hours=48)
        db.upsert_job(old_job)

        new_job = _make_job("new1")
        new_job.date_scraped = datetime.now()
        db.upsert_job(new_job)

        cutoff = datetime.now() - timedelta(hours=24)
        recent = db.get_jobs(since=cutoff, limit=None)
        assert len(recent) == 1
        assert recent[0].source_id == "new1"

    def test_since_none_returns_all(self, db):
        """get_jobs(since=None) returns all jobs (backward compat)."""
        db.upsert_job(_make_job("a"))
        db.upsert_job(_make_job("b"))
        jobs = db.get_jobs(since=None, limit=None)
        assert len(jobs) == 2

    def test_since_combines_with_min_score(self, db):
        """since and min_score filters combine correctly."""
        from datetime import datetime, timedelta

        j1 = _make_job("high_new", score=80)
        j1.date_scraped = datetime.now()
        db.upsert_job(j1)

        j2 = _make_job("low_new", score=20)
        j2.date_scraped = datetime.now()
        db.upsert_job(j2)

        j3 = _make_job("high_old", score=80)
        j3.date_scraped = datetime.now() - timedelta(hours=48)
        db.upsert_job(j3)

        cutoff = datetime.now() - timedelta(hours=24)
        jobs = db.get_jobs(since=cutoff, min_score=55, limit=None)
        assert len(jobs) == 1
        assert jobs[0].source_id == "high_new"


class TestGetAlertStats:
    def test_returns_correct_counts(self, db):
        from datetime import datetime, timedelta

        # Recent high-score job
        j1 = _make_job("h1", score=70, status="new")
        j1.date_scraped = datetime.now()
        db.upsert_job(j1)

        # Recent medium-score job
        j2 = _make_job("m1", score=45, status="new")
        j2.date_scraped = datetime.now()
        db.upsert_job(j2)

        # Recent low-score job
        j3 = _make_job("l1", score=20, status="new")
        j3.date_scraped = datetime.now()
        db.upsert_job(j3)

        # Old high-score job (still status=new)
        j4 = _make_job("old1", score=80, status="new")
        j4.date_scraped = datetime.now() - timedelta(hours=48)
        db.upsert_job(j4)

        stats = db.get_alert_stats(since_hours=24, score_threshold=55)
        assert stats["total_new"] == 4  # all have status=new
        assert stats["scraped_24h"] == 3  # 3 recent
        assert stats["high_count"] == 1  # j1 only (70 >= 55, recent)
        assert stats["medium_count"] == 1  # j2 only (45 >= 40 and < 55, recent)

    def test_custom_threshold(self, db):
        from datetime import datetime

        j1 = _make_job("t1", score=50, status="new")
        j1.date_scraped = datetime.now()
        db.upsert_job(j1)

        stats = db.get_alert_stats(since_hours=24, score_threshold=40)
        assert stats["high_count"] == 1  # 50 >= 40
        assert stats["medium_count"] == 0  # nothing in 40-39 range (impossible)

    def test_empty_db(self, db):
        stats = db.get_alert_stats()
        assert stats["total_new"] == 0
        assert stats["scraped_24h"] == 0
        assert stats["high_count"] == 0
        assert stats["medium_count"] == 0


class TestGetDailyTrend:
    def test_returns_per_day_breakdown(self, db):
        from datetime import datetime, timedelta

        today = datetime.now()
        yesterday = today - timedelta(days=1)

        j1 = _make_job("d1", score=70)
        j1.date_scraped = today
        db.upsert_job(j1)

        j2 = _make_job("d2", score=45)
        j2.date_scraped = today
        db.upsert_job(j2)

        j3 = _make_job("d3", score=60)
        j3.date_scraped = yesterday
        db.upsert_job(j3)

        trend = db.get_daily_trend(days=7, score_threshold=55)
        assert len(trend) == 2

        # Results ordered by day DESC
        today_row = trend[0]
        assert today_row["total"] == 2
        assert today_row["high"] == 1  # 70 >= 55
        assert today_row["medium"] == 1  # 45 >= 40 and < 55

        yesterday_row = trend[1]
        assert yesterday_row["total"] == 1
        assert yesterday_row["high"] == 1  # 60 >= 55

    def test_empty_db(self, db):
        trend = db.get_daily_trend(days=7, score_threshold=55)
        assert trend == []
