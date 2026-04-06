"""Tests for the export module and CLI command."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_scout.export import write_csv, write_json
from job_scout.models import Compensation, CompInterval, Job, Location, Site

runner = CliRunner()


def _make_job(
    *,
    id: int = 1,
    company: str = "Acme",
    title: str = "Engineer",
    score: int = 75,
    source: Site = Site.LINKEDIN,
    status: str = "new",
    date_posted: date | None = date(2026, 4, 1),
    location: Location | None = None,
    compensation: Compensation | None = None,
    score_breakdown: dict | None = None,
) -> Job:
    return Job(
        id=id,
        source=source,
        source_id=f"test-{id}",
        url=f"https://example.com/jobs/{id}",
        title=title,
        company=company,
        location=location or Location(city="NYC", state="NY"),
        compensation=compensation,
        date_posted=date_posted,
        score=score,
        score_breakdown=score_breakdown or {"keyword": 40},
        status=status,
    )


# --- write_csv tests ---


class TestWriteCSV:
    def test_basic_csv(self, tmp_path: Path):
        jobs = [_make_job(), _make_job(id=2, company="Beta")]
        path = tmp_path / "out.csv"
        count = write_csv(jobs, path)

        assert count == 2
        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["company"] == "Acme"
        assert rows[1]["company"] == "Beta"

    def test_csv_fields(self, tmp_path: Path):
        job = _make_job(
            compensation=Compensation(
                min_amount=100000, max_amount=150000, interval=CompInterval.YEARLY
            )
        )
        path = tmp_path / "out.csv"
        write_csv([job], path)

        with open(path) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["id"] == "1"
        assert row["score"] == "75"
        assert row["location"] == "NYC, NY"
        assert row["salary"] == "$100,000 - $150,000 /yearly"
        assert row["date_posted"] == "2026-04-01"
        assert row["source"] == "linkedin"
        assert row["status"] == "new"
        assert "score_breakdown" not in row

    def test_csv_none_location_and_comp(self, tmp_path: Path):
        job = _make_job(location=Location(), compensation=None)
        path = tmp_path / "out.csv"
        write_csv([job], path)

        with open(path) as f:
            row = list(csv.DictReader(f))[0]
        assert row["location"] == "Unknown"
        assert row["salary"] == ""

    def test_csv_empty_list(self, tmp_path: Path):
        path = tmp_path / "empty.csv"
        count = write_csv([], path)
        assert count == 0
        with open(path) as f:
            reader = csv.DictReader(f)
            assert list(reader) == []


# --- write_json tests ---


class TestWriteJSON:
    def test_basic_json(self, tmp_path: Path):
        jobs = [_make_job(), _make_job(id=2, company="Beta")]
        path = tmp_path / "out.json"
        count = write_json(jobs, path)

        assert count == 2
        data = json.loads(path.read_text())
        assert len(data) == 2
        assert data[0]["company"] == "Acme"
        assert data[1]["company"] == "Beta"

    def test_json_includes_score_breakdown(self, tmp_path: Path):
        job = _make_job(score_breakdown={"keyword": 40, "company": 10})
        path = tmp_path / "out.json"
        write_json([job], path)

        data = json.loads(path.read_text())
        assert data[0]["score_breakdown"] == {"keyword": 40, "company": 10}

    def test_json_none_fields(self, tmp_path: Path):
        job = _make_job(date_posted=None, compensation=None)
        path = tmp_path / "out.json"
        write_json([job], path)

        data = json.loads(path.read_text())
        assert data[0]["date_posted"] == ""
        assert data[0]["salary"] == ""

    def test_json_empty_list(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        count = write_json([], path)
        assert count == 0
        assert json.loads(path.read_text()) == []


# --- CLI export command tests ---


class TestExportCLI:
    @pytest.fixture()
    def populated_db(self, tmp_path: Path, monkeypatch):
        """Create a DB with test jobs and patch config/db helpers."""
        from job_scout.db import JobDB

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        jobs = [
            _make_job(
                id=None,
                score=80,
                company="Alpha",
                source=Site.LINKEDIN,
                date_posted=date(2026, 4, 5),
            ),
            _make_job(
                id=None,
                score=50,
                company="Beta",
                source=Site.INDEED,
                status="applied",
                date_posted=date(2026, 3, 20),
            ),
            _make_job(
                id=None,
                score=30,
                company="Gamma",
                source=Site.GOOGLE,
                status="filtered",
                date_posted=None,
            ),
        ]
        for job in jobs:
            db.upsert_job(job)
        db.close()

        from unittest.mock import MagicMock

        from job_scout.config import AppConfig

        mock_cfg = MagicMock(spec=AppConfig)
        mock_cfg.db_path = db_path

        monkeypatch.setattr("job_scout.cli._get_config", lambda: mock_cfg)
        monkeypatch.setattr("job_scout.cli._get_db", lambda cfg=None: JobDB(db_path))
        return tmp_path

    def test_export_csv_default(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "export.csv"
        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 0
        assert "Exported 3 jobs" in result.output
        with open(out) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 3

    def test_export_json_by_extension(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "export.json"
        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 0
        data = json.loads(out.read_text())
        assert len(data) == 3
        assert "score_breakdown" in data[0]

    def test_export_format_overrides_extension(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "export.json"
        result = runner.invoke(app, ["export", "--output", str(out), "--format", "csv"])
        assert result.exit_code == 0
        # Should be CSV despite .json extension
        with open(out) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 3

    def test_export_status_filter(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "out.csv"
        result = runner.invoke(
            app, ["export", "--output", str(out), "--status", "applied"]
        )
        assert result.exit_code == 0
        assert "Exported 1 jobs" in result.output

    def test_export_min_score(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "out.csv"
        result = runner.invoke(
            app, ["export", "--output", str(out), "--min-score", "60"]
        )
        assert result.exit_code == 0
        assert "Exported 1 jobs" in result.output

    def test_export_company_filter(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "out.csv"
        result = runner.invoke(
            app, ["export", "--output", str(out), "--company", "Alpha"]
        )
        assert result.exit_code == 0
        assert "Exported 1 jobs" in result.output

    def test_export_source_filter(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "out.csv"
        result = runner.invoke(
            app, ["export", "--output", str(out), "--source", "indeed"]
        )
        assert result.exit_code == 0
        assert "Exported 1 jobs" in result.output

    def test_export_days_filter(self, populated_db: Path, monkeypatch):
        from job_scout.cli import app

        monkeypatch.setattr(
            "job_scout.cli.date",
            type(
                "MockDate", (date,), {"today": staticmethod(lambda: date(2026, 4, 6))}
            ),
        )

        out = populated_db / "out.csv"
        result = runner.invoke(app, ["export", "--output", str(out), "--days", "7"])
        assert result.exit_code == 0
        # Alpha (Apr 5) + Gamma (null date passes) = 2
        assert "Exported 2 jobs" in result.output

    def test_export_since_until(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "out.csv"
        result = runner.invoke(
            app,
            [
                "export",
                "--output",
                str(out),
                "--since",
                "2026-04-01",
                "--until",
                "2026-04-06",
            ],
        )
        assert result.exit_code == 0
        # Alpha (Apr 5) + Gamma (null passes) = 2
        assert "Exported 2 jobs" in result.output

    def test_export_days_and_since_mutually_exclusive(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "out.csv"
        result = runner.invoke(
            app,
            ["export", "--output", str(out), "--days", "7", "--since", "2026-04-01"],
        )
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_export_no_matches(self, populated_db: Path):
        from job_scout.cli import app

        out = populated_db / "out.csv"
        result = runner.invoke(
            app, ["export", "--output", str(out), "--min-score", "99"]
        )
        assert result.exit_code == 0
        assert "No jobs found matching filters" in result.output
        assert not out.exists()
