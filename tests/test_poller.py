"""Tests for M6: polling orchestration (run_poll / run_resolve)."""

from __future__ import annotations

from datetime import datetime

from unittest.mock import patch

import pytest

from job_scout.config import AppConfig, DiscoveryConfig, ScrapingConfig
from job_scout.db import JobDB
from job_scout.models import ATSProvider, Company


@pytest.fixture()
def db(tmp_path):
    db = JobDB(tmp_path / "test.db")
    yield db
    db.close()


def _cfg() -> AppConfig:
    return AppConfig(
        profile={"name": "T", "target_title": "X"},
        search={"terms": ["sw"], "locations": ["Sydney"]},
        discovery=DiscoveryConfig(enabled=True, missing_crawls_before_close=3),
    )


def _company(**kw) -> Company:
    defaults = dict(
        name="Canva",
        domain="canva.com",
        ats=ATSProvider.GREENHOUSE,
        ats_slug="canva",
        discovered_from=["blackbird"],
    )
    defaults.update(kw)
    return Company(**defaults)


class TestRunPoll:
    def test_polls_and_persists(self, db, tmp_path):
        from job_scout.poller import run_poll

        db.upsert_company(_company())
        cfg = _cfg()

        class FakeAdapter:
            def __init__(self, config):
                self.config = config

            def poll(self, company):
                from job_scout.models import ATSJob, Location, Site

                return [
                    ATSJob(
                        source=Site.GREENHOUSE,
                        source_id="j1",
                        company_id=company.id,
                        company=company.name,
                        title="Software Engineer",
                        url="https://x/j1",
                        location=Location(city="Sydney", state="NSW", country="AU"),
                        description="python backend",
                        updated_at=datetime.now(),
                    )
                ]

        with patch("job_scout.poller.get_adapter", return_value=FakeAdapter):
            summary = run_poll(
                cfg,
                db,
                scraping=ScrapingConfig(
                    delay_min_seconds=0,
                    delay_max_seconds=0,
                    max_retries=0,
                    min_request_interval_seconds=0,
                ),
            )

        assert summary["companies"] == 1
        assert summary["jobs"] == 1
        assert summary["new_source"] == 1
        assert summary["new_canonical"] == 1

        # Canonical persisted.
        canonicals = db.get_canonical_jobs()
        assert len(canonicals) == 1
        assert canonicals[0].company == "Canva"
        assert canonicals[0].source_ids == ["greenhouse:j1"]

    def test_second_poll_updates_not_duplicates(self, db):
        from job_scout.poller import run_poll

        db.upsert_company(_company())
        cfg = _cfg()

        class FakeAdapter:
            def __init__(self, config):
                self.config = config

            def poll(self, company):
                from job_scout.models import ATSJob, Location, Site

                return [
                    ATSJob(
                        source=Site.GREENHOUSE,
                        source_id="j1",
                        company_id=company.id,
                        company=company.name,
                        title="Software Engineer",
                        url="https://x/j1",
                        location=Location(city="Sydney", state="NSW", country="AU"),
                        description="python backend",
                        updated_at=datetime.now(),
                    )
                ]

        with patch("job_scout.poller.get_adapter", return_value=FakeAdapter):
            first = run_poll(
                cfg,
                db,
                scraping=ScrapingConfig(
                    delay_min_seconds=0,
                    delay_max_seconds=0,
                    max_retries=0,
                    min_request_interval_seconds=0,
                ),
            )
            second = run_poll(
                cfg,
                db,
                scraping=ScrapingConfig(
                    delay_min_seconds=0,
                    delay_max_seconds=0,
                    max_retries=0,
                    min_request_interval_seconds=0,
                ),
            )

        assert first["new_source"] == 1
        assert second["new_source"] == 0
        assert second["new_canonical"] == 0
        assert len(db.get_canonical_jobs()) == 1


class TestCloseDetectionAndRepost:
    def _seed_open_job(self, db):
        from job_scout.poller import run_poll

        db.upsert_company(_company())

        class FilledAdapter:
            def __init__(self, config):
                self.config = config

            def poll(self, company):
                from job_scout.models import ATSJob, Location, Site

                return [
                    ATSJob(
                        source=Site.GREENHOUSE,
                        source_id="j1",
                        company_id=company.id,
                        company=company.name,
                        title="Software Engineer",
                        url="https://x/j1",
                        location=Location(city="Sydney", state="NSW", country="AU"),
                        description="python backend",
                        updated_at=datetime.now(),
                    )
                ]

        with patch("job_scout.poller.get_adapter", return_value=FilledAdapter):
            return run_poll(
                _cfg(),
                db,
                scraping=ScrapingConfig(
                    delay_min_seconds=0,
                    delay_max_seconds=0,
                    max_retries=0,
                    min_request_interval_seconds=0,
                ),
            )

    def test_job_closed_after_missing_crawls_then_reopened_with_repost(self, db):
        """A job missing past the missing_crawls_before_close window is closed
        by run_poll, and a reappearing requisition reopens with repost=True."""
        from job_scout.poller import run_poll

        first = self._seed_open_job(db)
        assert first["new_canonical"] == 1
        canon = db.get_canonical_jobs()[0]
        assert canon.status == "open"
        assert canon.repost is False

        # Backdate last_seen_at far enough that `missing_crawls_before_close=3`
        # at poll_interval 12h (cutoff = now - 2*12h) declares it stale.
        db.conn.execute(
            "UPDATE canonical_jobs SET last_seen_at = ?",
            (datetime.now().replace(year=2015).isoformat(),),
        )
        db.conn.execute(
            "UPDATE ats_jobs SET last_seen_at = ?",
            (datetime.now().replace(year=2015).isoformat(),),
        )
        db.conn.commit()

        class EmptyAdapter:
            def __init__(self, config):
                self.config = config

            def poll(self, company):
                return []

        with patch("job_scout.poller.get_adapter", return_value=EmptyAdapter):
            missing = run_poll(
                _cfg(),
                db,
                scraping=ScrapingConfig(
                    delay_min_seconds=0,
                    delay_max_seconds=0,
                    max_retries=0,
                    min_request_interval_seconds=0,
                ),
            )

        assert missing["closed_canonical"] == 1
        closed = db.get_canonical_jobs()[0]
        assert closed.status == "closed"
        assert closed.closed_at is not None

        class RepostAdapter:
            def __init__(self, config):
                self.config = config

            def poll(self, company):
                from job_scout.models import ATSJob, Location, Site

                return [
                    ATSJob(
                        source=Site.GREENHOUSE,
                        source_id="j1",
                        company_id=company.id,
                        company=company.name,
                        title="Software Engineer",
                        url="https://x/j1",
                        location=Location(city="Sydney", state="NSW", country="AU"),
                        description="python backend",
                        updated_at=datetime.now(),
                    )
                ]

        # The requisition reappears -> reopened and flagged as a sticky repost.
        with patch("job_scout.poller.get_adapter", return_value=RepostAdapter):
            reopened = run_poll(
                _cfg(),
                db,
                scraping=ScrapingConfig(
                    delay_min_seconds=0,
                    delay_max_seconds=0,
                    max_retries=0,
                    min_request_interval_seconds=0,
                ),
            )

        assert reopened["new_canonical"] == 0
        reposted = db.get_canonical_jobs()[0]
        assert reposted.status == "open"
        assert reposted.repost is True
        # Coverage counts the reopened requisition as open, not closed.
        gr = db.conn.execute(
            "SELECT COUNT(*) FROM canonical_jobs WHERE status = 'open'"
        ).fetchone()[0]
        gc = db.conn.execute(
            "SELECT COUNT(*) FROM canonical_jobs WHERE status = 'closed'"
        ).fetchone()[0]
        assert (gr, gc) == (1, 0)


class TestRunResolve:
    def test_resolves_pending_companies(self, db, tmp_path):
        from job_scout.poller import run_resolve

        db.upsert_company(
            _company(name="UnresolvedCo", ats=ATSProvider.UNKNOWN, ats_slug=None)
        )
        cfg = _cfg()

        class FakeSource:
            name = "fake"

            def __init__(self, cfg):
                self.config = cfg

            def discover(self):
                return []

        class FakeResolver:
            def __init__(self, cfg):
                pass

            def resolve(self, company):
                out = company.model_copy(deep=True)
                if company.name == "UnresolvedCo":
                    out.ats = ATSProvider.GREENHOUSE
                    out.ats_slug = "unresolvedco"
                return out

        with (
            patch(
                "job_scout.poller.run_discovery",
                return_value={"candidates": 0, "added": 0, "updated": 0},
            ),
            patch("job_scout.poller.CompanyResolver", FakeResolver),
        ):
            summary = run_resolve(
                cfg,
                db,
                scraping=ScrapingConfig(
                    delay_min_seconds=0,
                    delay_max_seconds=0,
                    max_retries=0,
                    min_request_interval_seconds=0,
                ),
            )

        assert summary["resolved"] == 1
        c = db.get_company_by_name("UnresolvedCo")
        assert c is not None
        assert c.ats == ATSProvider.GREENHOUSE
        assert c.ats_slug == "unresolvedco"
