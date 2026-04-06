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
