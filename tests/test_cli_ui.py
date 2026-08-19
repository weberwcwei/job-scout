"""Tests for the local web UI (job-scout ui): API endpoints and status updates."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from job_scout.db import JobDB
from job_scout.models import Job, Location, Site
from job_scout.ui import _create_server


def _make_job(
    source_id: str,
    *,
    source: str = "linkedin",
    score: int = 50,
    status: str = "new",
    title: str = "Software Engineer",
    company: str = "TestCo",
) -> Job:
    return Job(
        source=Site(source),
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=title,
        company=company,
        location=Location(city="SF", state="CA"),
        description="A job description with enough length to be useful.",
        score=score,
        score_breakdown={"keyword": score},
        status=status,
    )


@pytest.fixture()
def db(tmp_path):
    db = JobDB(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture()
def server(db):
    """Run a real UI server on an ephemeral port against the tmp DB."""
    httpd, _ = _create_server(db.db_path, "127.0.0.1", 0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def _get(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 - test-only localhost
        return resp.status, json.loads(resp.read())


def _patch(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - test-only localhost
        return resp.status, json.loads(resp.read())


class TestMeta:
    def test_meta_counts_statuses_and_sources(self, db, server):
        db.upsert_job(_make_job("a1", source="linkedin", score=80, status="new"))
        db.upsert_job(_make_job("a2", source="indeed", score=60, status="applied"))
        db.upsert_job(_make_job("a3", source="linkedin", score=10, status="rejected"))

        status, data = _get(f"{server}/api/meta")
        assert status == 200
        counts = {s["status"]: s["count"] for s in data["statuses"]}
        assert counts["new"] == 1
        assert counts["applied"] == 1
        assert counts["rejected"] == 1
        assert counts["expired"] == 0
        assert counts["interview"] == 0
        assert set(data["sources"]) == {"linkedin", "indeed"}
        assert data["total"] == 3


class TestJobs:
    def test_lists_sorted_by_score_desc(self, db, server):
        db.upsert_job(_make_job("a1", score=30))
        db.upsert_job(_make_job("a2", score=90))
        db.upsert_job(_make_job("a3", score=55))

        _, data = _get(f"{server}/api/jobs")
        scores = [j["score"] for j in data["jobs"]]
        assert scores == [90, 55, 30]

    def test_status_filter(self, db, server):
        db.upsert_job(_make_job("a1", status="new"))
        db.upsert_job(_make_job("a2", status="applied"))
        db.upsert_job(_make_job("a3", status="rejected"))

        _, data = _get(f"{server}/api/jobs?status=applied")
        assert [j["url"] for j in data["jobs"]] == ["https://example.com/a2"]

    def test_expired_status_filter(self, db, server):
        db.upsert_job(_make_job("a1", status="new"))
        _, expired_id = db.upsert_job(_make_job("a2", status="new"))
        db.conn.execute(
            "UPDATE jobs SET status = 'expired' WHERE id = ?", (expired_id,)
        )
        db.conn.commit()

        _, data = _get(f"{server}/api/jobs?status=expired")

        assert [job["status"] for job in data["jobs"]] == ["expired"]

    def test_search_filter(self, db, server):
        db.upsert_job(_make_job("a1", title="Backend Engineer", company="Acme"))
        db.upsert_job(_make_job("a2", title="Frontend Engineer", company="Beta"))

        _, data = _get(f"{server}/api/jobs?q=acme")
        assert [j["url"] for j in data["jobs"]] == ["https://example.com/a1"]

    def test_source_filter(self, db, server):
        db.upsert_job(_make_job("a1", source="linkedin"))
        db.upsert_job(_make_job("a2", source="indeed"))

        _, data = _get(f"{server}/api/jobs?source=indeed")
        assert [j["url"] for j in data["jobs"]] == ["https://example.com/a2"]

    def test_job_payload_shape(self, db, server):
        db.upsert_job(_make_job("a1", score=77))

        _, data = _get(f"{server}/api/jobs")
        job = data["jobs"][0]
        for key in ("id", "title", "company", "source", "url", "location",
                    "score", "status", "date_posted", "description"):
            assert key in job


class TestPatch:
    def test_update_status(self, db, server):
        _, inserted = db.upsert_job(_make_job("a1", status="new"))
        job_id = inserted

        status, data = _patch(f"{server}/api/jobs/{job_id}", {"status": "applied"})
        assert status == 200
        assert data["ok"] is True
        assert data["job"]["status"] == "applied"

        refreshed = db.get_job(job_id)
        assert refreshed is not None
        assert refreshed.status == "applied"

    def test_rejected_demotes_score(self, db, server):
        _, inserted = db.upsert_job(_make_job("a1", score=80, status="new"))
        job_id = inserted

        _, data = _patch(f"{server}/api/jobs/{job_id}", {"status": "rejected"})
        assert data["job"]["score"] == 0

    def test_invalid_status_rejected(self, db, server):
        _, inserted = db.upsert_job(_make_job("a1"))
        job_id = inserted

        req = urllib.request.Request(
            f"{server}/api/jobs/{job_id}",
            data=json.dumps({"status": "banana"}).encode(),
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)  # noqa: S310 - test-only localhost
        assert exc_info.value.code == 400

    def test_expired_status_cannot_be_set_manually(self, db, server):
        _, job_id = db.upsert_job(_make_job("a1"))
        req = urllib.request.Request(
            f"{server}/api/jobs/{job_id}",
            data=json.dumps({"status": "expired"}).encode(),
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)  # noqa: S310 - test-only localhost

        assert exc_info.value.code == 400

    def test_unknown_job_404(self, server):
        req = urllib.request.Request(
            f"{server}/api/jobs/99999",
            data=json.dumps({"status": "applied"}).encode(),
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)  # noqa: S310 - test-only localhost
        assert exc_info.value.code == 404

    def test_notes_update(self, db, server):
        _, inserted = db.upsert_job(_make_job("a1"))
        job_id = inserted

        _, data = _patch(
            f"{server}/api/jobs/{job_id}", {"status": "applied", "notes": "phone screen"}
        )
        assert data["job"]["notes"] == "phone screen"


class TestStatic:
    def test_index_served(self, server):
        with urllib.request.urlopen(f"{server}/") as resp:  # noqa: S310
            assert resp.status == 200
            assert resp.headers.get_content_type() == "text/html"
            assert resp.read()

    @pytest.mark.parametrize(
        ("path", "content_type"),
        [("app.css", "text/css"), ("app.js", "text/javascript")],
    )
    def test_assets_served(self, server, path, content_type):
        with urllib.request.urlopen(f"{server}/{path}") as resp:  # noqa: S310
            assert resp.status == 200
            assert resp.headers.get_content_type() == content_type
            assert resp.read()

    def test_unknown_path_404(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{server}/nope")  # noqa: S310
        assert exc_info.value.code == 404
