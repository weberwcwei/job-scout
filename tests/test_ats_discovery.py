"""Tests for the ATS discovery subsystem foundations (M0).

Covers the Company/ATSJob models, the companies + ats_jobs storage tables,
and the discovery config section.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from job_scout.config import AppConfig, DiscoveryConfig
from job_scout.db import JobDB
from job_scout.models import ATSJob, ATSProvider, Company, Location, Site


@pytest.fixture()
def db(tmp_path):
    db = JobDB(tmp_path / "test.db")
    yield db
    db.close()


def _make_company(name: str = "Canva", **kw) -> Company:
    defaults = dict(
        name=name,
        domain="canva.com",
        careers_url="https://www.canva.com/careers",
        ats=ATSProvider.GREENHOUSE,
        ats_slug="canva",
        discovered_from=["airtree", "funding_news"],
        last_verified_at=datetime(2026, 8, 26, 10, 0, 0),
    )
    defaults.update(kw)
    return Company(**defaults)


def _make_ats_job(source_id: str = "12345", **kw) -> ATSJob:
    defaults = dict(
        source=Site.GREENHOUSE,
        source_id=source_id,
        company_id=1,
        company="Canva",
        title="Software Engineer",
        url="https://boards.greenhouse.io/canva/jobs/12345",
        apply_url="https://boards.greenhouse.io/canva/jobs/12345#app",
        location_text="Sydney, NSW",
        location=Location(city="Sydney", state="NSW", country="AU"),
        hybrid=True,
        employment_type="full_time",
        description="A full software engineering role at Canva.",
        salary_min=120000,
        salary_max=180000,
        currency="AUD",
        posted_at=datetime(2026, 8, 25, 9, 0, 0),
    )
    defaults.update(kw)
    return ATSJob(**defaults)


class TestCompanyModel:
    def test_defaults(self):
        c = Company(name="Example")
        assert c.ats == ATSProvider.UNKNOWN
        assert c.discovered_from == []
        assert c.domain is None
        assert c.created_at is not None

    def test_full(self):
        c = _make_company()
        assert c.ats == ATSProvider.GREENHOUSE
        assert c.ats_slug == "canva"
        assert c.discovered_from == ["airtree", "funding_news"]


class TestATSJobModel:
    def test_extends_job(self):
        j = _make_ats_job()
        assert isinstance(j, ATSJob)
        assert j.dedup_key  # 16-hex hash of source:source_id
        assert j.repost is False
        assert j.status == "open"

    def test_repost_flag(self):
        j = _make_ats_job(repost=True)
        assert j.repost is True

    def test_dedup_key_is_source_scoped(self):
        a = _make_ats_job("x")
        b = _make_ats_job("y")
        assert a.dedup_key != b.dedup_key


class TestCompanyStorage:
    def test_upsert_inserts(self, db):
        is_new, cid = db.upsert_company(_make_company())
        assert is_new is True
        c = db.get_company(cid)
        assert c.name == "Canva"
        assert c. ats == ATSProvider.GREENHOUSE
        assert c.ats_slug == "canva"
        assert c.discovered_from == ["airtree", "funding_news"]

    def test_upsert_updates_existing(self, db):
        db.upsert_company(_make_company())
        is_new, cid = db.upsert_company(
            _make_company(ats=ATSProvider.LEVER, ats_slug="canva-2")
        )
        assert is_new is False
        c = db.get_company(cid)
        assert c.ats == ATSProvider.LEVER
        assert c.ats_slug == "canva-2"

    def test_get_by_name(self, db):
        db.upsert_company(_make_company())
        c = db.get_company_by_name("Canva")
        assert c is not None
        assert c.domain == "canva.com"

    def test_get_pollable_companies(self, db):
        db.upsert_company(_make_company())
        db.upsert_company(_make_company(name="UnknownCo", ats=ATSProvider.UNKNOWN, ats_slug=None))
        pollable = db.get_pollable_companies()
        assert [c.name for c in pollable] == ["Canva"]


class TestATSJobStorage:
    def test_upsert_inserts(self, db):
        db.upsert_company(_make_company())
        is_new, jid = db.upsert_ats_job(_make_ats_job())
        assert is_new is True
        j = db.get_ats_job(jid)
        assert j.title == "Software Engineer"
        assert j.company_id == 1
        assert j.location.city == "Sydney"
        assert j.hybrid is True
        assert j.salary_min == 120000
        assert j.currency == "AUD"

    def test_upsert_updates_and_keeps_first_seen(self, db):
        db.upsert_company(_make_company())
        first = _make_ats_job()
        db.upsert_ats_job(first)
        later = _make_ats_job(
            last_seen_at=datetime.now() + timedelta(days=1),
            updated_at=datetime.now() + timedelta(days=1),
            title="Senior Software Engineer",
        )
        is_new, jid = db.upsert_ats_job(later)
        assert is_new is False
        j = db.get_ats_job(jid)
        assert j.title == "Senior Software Engineer"
        assert j.first_seen_at == first.first_seen_at  # append-only: first_seen preserved

    def test_close_missing_jobs(self, db):
        db.upsert_company(_make_company())
        db.upsert_ats_job(_make_ats_job(last_seen_at=datetime(2026, 8, 20)))
        cutoff = datetime(2026, 8, 26)
        closed = db.close_ats_jobs_missing_since(cutoff)
        assert closed == 1
        j = db.get_ats_jobs(status="closed")[0]
        assert j.status == "closed"
        assert j.closed_at is not None

    def test_get_ats_jobs_by_company(self, db):
        db.upsert_company(_make_company())
        db.upsert_company(_make_company(name="OtherCo"))
        db.upsert_ats_job(_make_ats_job(company_id=1))
        db.upsert_ats_job(_make_ats_job(source_id="2", company_id=2))
        jobs = db.get_ats_jobs(company_id=1)
        assert len(jobs) == 1
        assert jobs[0].company_id == 1


class TestDiscoveryConfig:
    def test_defaults(self):
        d = DiscoveryConfig()
        assert d.enabled is False
        assert "blackbird" in d.vc_portfolios
        assert d.missing_crawls_before_close == 3

    def test_parses_from_yaml(self):
        cfg = AppConfig(
            profile={"name": "T", "target_title": "X"},
            search={"terms": ["sw"], "locations": ["Sydney"]},
            discovery={"enabled": True, "missing_crawls_before_close": 5},
        )
        assert cfg.discovery.enabled is True
        assert cfg.discovery.missing_crawls_before_close == 5
