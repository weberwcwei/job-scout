"""Tests for M4: normalisation, dedup, and canonical history."""

from __future__ import annotations

from datetime import datetime

import pytest

from job_scout.db import JobDB
from job_scout.models import ATSJob, CanonicalJob, Location, Site


@pytest.fixture()
def db(tmp_path):
    db = JobDB(tmp_path / "test.db")
    yield db
    db.close()


def _job(
    source_id: str,
    *,
    source: Site = Site.GREENHOUSE,
    title: str = "Software Engineer",
    company: str = "Canva",
    city: str = "Sydney",
    remote: bool = False,
    updated: datetime | None = None,
    repost: bool = False,
) -> ATSJob:
    now = datetime(2026, 8, 26, 12, 0, 0)
    return ATSJob(
        source=source,
        source_id=source_id,
        company_id=1,
        company=company,
        title=title,
        url=f"https://x/{source_id}",
        location=Location(city=city, state="NSW", country="AU", is_remote=remote),
        description="A python backend role",
        posted_at=now,
        updated_at=updated or now,
        last_seen_at=updated or now,
        repost=repost,
    )




class TestNormalizeTitle:
    def test_strips_level_and_location(self):
        from job_scout.normalization.job import normalize_title

        assert normalize_title("Software Engineer II - Sydney") == "software engineer"
        assert normalize_title("Senior Backend Engineer (Remote)") == "backend engineer"

    def test_keeps_core(self):
        from job_scout.normalization.job import normalize_title

        assert normalize_title("Data Scientist") == "data scientist"


class TestCanonicalKey:
    def test_variants_collapse(self):
        from job_scout.normalization.job import canonical_key

        a = _job("1", title="Software Engineer II", company="Canva")
        b = _job("2", title="Software Engineer 2 - Sydney", company="Canva")
        assert canonical_key(a) == canonical_key(b)

    def test_different_role_differs(self):
        from job_scout.normalization.job import canonical_key

        a = _job("1", title="Software Engineer", company="Canva")
        b = _job("2", title="Data Scientist", company="Canva")
        assert canonical_key(a) != canonical_key(b)

    def test_remote_and_city_collapse(self):
        from job_scout.normalization.job import canonical_key

        a = _job("1", title="Engineer", company="Canva", city="Sydney")
        b = _job("2", title="Engineer", company="Canva", city="Melbourne", remote=True)
        # Different city/remote -> different key (they are distinct roles).
        assert canonical_key(a) != canonical_key(b)


class TestProjectMerge:
    def test_project_sets_fields(self):
        from job_scout.normalization.job import project

        job = _job("1")
        c = project(job)
        assert isinstance(c, CanonicalJob)
        assert c.canonical_key
        assert c.company == "Canva"
        assert c.source_ids == ["greenhouse:1"]

    def test_merge_prefers_newest_and_unions_source_ids(self):
        from job_scout.normalization.job import merge, project

        older = _job("1", source=Site.GREENHOUSE, updated=datetime(2026, 8, 20))
        newer = _job("2", source=Site.LEVER, title="Software Engineer II", updated=datetime(2026, 8, 25))
        c = merge(project(older), newer)
        assert c.title == "Software Engineer II"
        assert "greenhouse:1" in c.source_ids
        assert "lever:2" in c.source_ids

    def test_merge_repost_is_sticky(self):
        from job_scout.normalization.job import merge, project

        a = _job("1", repost=False)
        b = _job("2", repost=True)
        c = merge(project(a), b)
        assert c.repost is True


class TestCanonicalStorage:
    def test_upsert_and_get(self, db):
        from job_scout.normalization.job import project

        job = _job("1")
        c = project(job)
        is_new, cid = db.upsert_canonical(c)
        assert is_new is True
        got = db.get_canonical(c.canonical_key)
        assert got is not None
        assert got.company == "Canva"
        assert got.id == cid

    def test_upsert_preserves_first_seen(self, db):
        from job_scout.normalization.job import project

        first = _job("1", updated=datetime(2026, 8, 20))
        db.upsert_canonical(project(first))
        later = _job("1", updated=datetime(2026, 8, 25))
        db.upsert_canonical(project(later))
        got = db.get_canonical(project(later).canonical_key)
        assert got.first_seen_at == first.first_seen_at

    def test_close_missing(self, db):
        from job_scout.normalization.job import project

        db.upsert_canonical(project(_job("1", updated=datetime(2026, 8, 20))))
        closed = db.close_canonical_jobs_missing_since(datetime(2026, 8, 26))
        assert closed == 1
        jobs = db.get_canonical_jobs(status="closed")
        assert len(jobs) == 1
        assert jobs[0].closed_at is not None