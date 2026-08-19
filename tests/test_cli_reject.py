"""Tests for the `job-scout reject` CLI command (single job and --company)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from job_scout.cli import app
from job_scout.config import AppConfig
from job_scout.db import JobDB
from job_scout.models import Job, Location, Site

runner = CliRunner()

MINIMAL_RAW = {
    "profile": {
        "name": "Test",
        "target_title": "Software Engineer",
        "keywords": {"critical": ["python"], "strong": [], "moderate": [], "weak": []},
        "target_companies": {"tier1": [], "tier2": [], "tier3": []},
        "title_signals": [],
        "dealbreakers": {
            "title_patterns": [],
            "company_patterns": [],
            "description_patterns": [],
        },
    },
    "search": {"terms": ["python"], "locations": ["Remote"], "sites": ["linkedin"]},
}


def _make_job(
    source_id: str,
    *,
    company: str = "TestCo",
    status: str = "new",
    score: int = 50,
) -> Job:
    return Job(
        source=Site.LINKEDIN,
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title="Software Engineer",
        company=company,
        location=Location(city="SF", state="CA"),
        description="python backend developer",
        score=score,
        score_breakdown={"keyword": score},
        status=status,
    )


@pytest.fixture()
def mock_env(tmp_path):
    db_path = tmp_path / "test.db"
    setup_db = JobDB(db_path)
    cfg = AppConfig(**MINIMAL_RAW)

    def _make_db(_cfg=None):
        return JobDB(db_path)

    with (
        patch("job_scout.cli._get_config", return_value=cfg),
        patch("job_scout.cli._get_db", side_effect=_make_db),
        patch("job_scout.config.LOG_DIR", tmp_path / "logs"),
    ):
        yield cfg, setup_db, tmp_path

    setup_db.close()


class TestRejectSingle:
    def test_rejects_and_demotes(self, mock_env):
        cfg, db, tmp_path = mock_env
        _, job_id = db.upsert_job(_make_job("s1", score=80))

        result = runner.invoke(app, ["reject", str(job_id), "--notes", "no fit"])
        assert result.exit_code == 0
        job = db.get_job(job_id)
        assert job.status == "rejected"
        assert job.score == 0
        assert job.notes == "no fit"

    def test_requires_id_or_company(self, mock_env):
        result = runner.invoke(app, ["reject"])
        assert result.exit_code == 1
        assert "exactly one" in result.output.lower()

    def test_unknown_id(self, mock_env):
        result = runner.invoke(app, ["reject", "9999"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRejectCompany:
    def test_rejects_all_matching_new_and_filtered(self, mock_env):
        cfg, db, tmp_path = mock_env
        db.upsert_job(_make_job("c1", company="Acme Corp"))
        db.upsert_job(_make_job("c2", company="Acme Inc", status="filtered"))
        db.upsert_job(_make_job("c3", company="Other Co"))
        db.upsert_job(_make_job("c4", company="Acme Corp", status="applied"))

        result = runner.invoke(app, ["reject", "--company", "acme"])
        assert result.exit_code == 0
        assert "Rejected 2 job(s)" in result.output

        statuses = {j.company: j.status for j in db.get_jobs(limit=None)}
        assert statuses["Acme Corp"] == "rejected"
        assert statuses["Acme Inc"] == "rejected"
        assert statuses["Other Co"] == "new"
        # user-tracked rows are never rejected by --company
        applied = [j for j in db.get_jobs(limit=None) if j.status == "applied"]
        assert len(applied) == 1 and applied[0].company == "Acme Corp"

    def test_dry_run_changes_nothing(self, mock_env):
        cfg, db, tmp_path = mock_env
        db.upsert_job(_make_job("d1", company="Acme Corp"))

        result = runner.invoke(app, ["reject", "--company", "acme", "--dry-run"])
        assert result.exit_code == 0
        assert "Would reject 1 job(s)" in result.output
        job = db.get_jobs(limit=None)[0]
        assert job.status == "new"
        assert job.score == 50

    def test_no_matches(self, mock_env):
        cfg, db, tmp_path = mock_env
        result = runner.invoke(app, ["reject", "--company", "nope"])
        assert result.exit_code == 0
        assert "No new/filtered jobs" in result.output
