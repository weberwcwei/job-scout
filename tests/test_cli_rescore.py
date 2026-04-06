"""Tests for the `job-scout rescore` CLI command."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from job_scout.cli import app
from job_scout.db import JobDB
from job_scout.models import Job, Location, Site
from job_scout.config import AppConfig


runner = CliRunner()

# Minimal valid config for testing
MINIMAL_RAW = {
    "profile": {
        "name": "Test",
        "target_title": "Software Engineer",
        "keywords": {
            "critical": ["python"],
            "strong": ["backend"],
            "moderate": [],
            "weak": [],
        },
        "target_companies": {"tier1": [], "tier2": [], "tier3": []},
        "title_signals": [],
        "dealbreakers": {
            "title_patterns": [],
            "company_patterns": [],
            "description_patterns": [],
        },
    },
    "search": {
        "terms": ["python"],
        "locations": ["Remote"],
        "sites": ["linkedin"],
    },
}


def _make_job(
    source_id: str,
    *,
    source: str = "linkedin",
    score: int = 50,
    status: str = "new",
    title: str = "Software Engineer",
    company: str = "TestCo",
    description: str = "python backend developer",
) -> Job:
    return Job(
        source=Site(source),
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=title,
        company=company,
        location=Location(city="SF", state="CA"),
        description=description,
        score=score,
        score_breakdown={"keyword": score},
        status=status,
    )


@pytest.fixture()
def mock_env(tmp_path):
    """Patch _get_config and _get_db so rescore uses our test db file."""
    db_path = tmp_path / "test.db"
    setup_db = JobDB(db_path)
    cfg = AppConfig(**MINIMAL_RAW)

    def _make_db(_cfg=None):
        return JobDB(db_path)

    with (
        patch("job_scout.cli._get_config", return_value=cfg),
        patch("job_scout.cli._get_db", side_effect=_make_db),
        patch("job_scout.scheduler.LOG_DIR", tmp_path / "logs"),
    ):
        yield cfg, setup_db, tmp_path

    setup_db.close()


class TestRescoreUpdatesScores:
    def test_rescore_updates_scores(self, mock_env):
        cfg, db, tmp_path = mock_env
        # Insert jobs with score=0 (description has "python" so scorer will give points)
        db.upsert_job(_make_job("r1", score=0, description="python backend developer"))
        db.upsert_job(_make_job("r2", score=0, description="python senior engineer"))

        result = runner.invoke(app, ["rescore"])
        assert result.exit_code == 0
        assert "changed" in result.output

        # Verify scores updated
        jobs = db.get_jobs(limit=None)
        assert all(j.score > 0 for j in jobs)

    def test_rescore_no_changes(self, mock_env):
        """When rescoring produces same scores, print no-changes message."""
        cfg, db, tmp_path = mock_env
        # Insert a job and manually set its score to what the scorer would give
        job = _make_job("nc1", score=0, description="python backend developer")
        db.upsert_job(job)

        # First rescore to set correct scores
        runner.invoke(app, ["rescore"])

        # Second rescore — should find no changes
        result = runner.invoke(app, ["rescore"])
        assert result.exit_code == 0
        assert "no score changes" in result.output


class TestRescoreFilters:
    def test_status_filter(self, mock_env):
        cfg, db, tmp_path = mock_env
        db.upsert_job(_make_job("sf1", score=0, status="new", description="python dev"))
        db.upsert_job(
            _make_job("sf2", score=0, status="applied", description="python dev")
        )

        result = runner.invoke(app, ["rescore", "--status", "new"])
        assert result.exit_code == 0

        # Only new job should be rescored
        new_jobs = db.get_jobs(status="new")
        applied_jobs = db.get_jobs(status="applied")
        assert all(j.score > 0 for j in new_jobs)
        assert all(j.score == 0 for j in applied_jobs)

    def test_site_filter(self, mock_env):
        cfg, db, tmp_path = mock_env
        db.upsert_job(
            _make_job("stf1", source="linkedin", score=0, description="python dev")
        )
        db.upsert_job(
            _make_job("stf2", source="indeed", score=0, description="python dev")
        )

        result = runner.invoke(app, ["rescore", "--site", "linkedin"])
        assert result.exit_code == 0

        linkedin = db.get_jobs(source="linkedin")
        indeed = db.get_jobs(source="indeed")
        assert all(j.score > 0 for j in linkedin)
        assert all(j.score == 0 for j in indeed)


class TestRescoreDryRun:
    def test_dry_run_does_not_persist(self, mock_env):
        cfg, db, tmp_path = mock_env
        db.upsert_job(_make_job("dr1", score=0, description="python dev"))

        result = runner.invoke(app, ["rescore", "--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output

        # Score should be unchanged in DB
        jobs = db.get_jobs(limit=None)
        assert all(j.score == 0 for j in jobs)


class TestRescoreOutput:
    def test_summary_output(self, mock_env):
        cfg, db, tmp_path = mock_env
        db.upsert_job(_make_job("so1", score=0, description="python dev"))
        db.upsert_job(_make_job("so2", score=0, description="python dev"))

        result = runner.invoke(app, ["rescore"])
        assert result.exit_code == 0
        assert "Rescored 2 jobs" in result.output
        assert "Avg shift" in result.output

    def test_output_truncation(self, mock_env):
        cfg, db, tmp_path = mock_env
        for i in range(30):
            db.upsert_job(_make_job(f"trunc{i}", score=0, description="python dev"))

        result = runner.invoke(app, ["rescore"])
        assert result.exit_code == 0
        # Should show "30 changed" in summary but table limited to 25 rows
        assert "30 changed" in result.output
        # Count table data rows (lines with → in them)
        arrow_lines = [line for line in result.output.split("\n") if "→" in line]
        assert len(arrow_lines) == 25

    def test_log_file_written(self, mock_env):
        cfg, db, tmp_path = mock_env
        db.upsert_job(_make_job("lf1", score=0, description="python dev"))

        result = runner.invoke(app, ["rescore"])
        assert result.exit_code == 0

        log_dir = tmp_path / "logs"
        log_files = list(log_dir.glob("rescore-*.log"))
        assert len(log_files) == 1
        content = log_files[0].read_text()
        assert "TestCo" in content

    def test_no_log_file_when_no_changes(self, mock_env):
        cfg, db, tmp_path = mock_env
        # Insert a job and set correct score first
        db.upsert_job(_make_job("nlf1", score=0, description="python dev"))
        runner.invoke(app, ["rescore"])

        # Clear any existing logs
        log_dir = tmp_path / "logs"
        for f in log_dir.glob("rescore-*.log"):
            f.unlink()

        # Second rescore — no changes, no log file
        result = runner.invoke(app, ["rescore"])
        assert "no score changes" in result.output
        assert list(log_dir.glob("rescore-*.log")) == []
