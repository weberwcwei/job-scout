"""Tests for content-based deduplication: compute_content_key, Job.content_key, DB dedup."""

from __future__ import annotations

from datetime import date

import pytest

from job_scout.db import JobDB
from job_scout.models import Job, Location, Site, compute_content_key


# ---------------------------------------------------------------------------
# compute_content_key() unit tests
# ---------------------------------------------------------------------------


class TestComputeContentKey:
    def test_deterministic(self):
        """Same inputs always produce the same key."""
        key1 = compute_content_key(
            "Engineer", "Acme", "SF", "CA", "2026-01-01", "A job desc"
        )
        key2 = compute_content_key(
            "Engineer", "Acme", "SF", "CA", "2026-01-01", "A job desc"
        )
        assert key1 == key2

    def test_is_16_hex_chars(self):
        key = compute_content_key("Engineer", "Acme", "SF", "CA", "2026-01-01", "desc")
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)

    def test_case_insensitive(self):
        """Normalization lowercases inputs."""
        key1 = compute_content_key("SOFTWARE ENGINEER", "ACME", "SF", "CA", "", "desc")
        key2 = compute_content_key("software engineer", "acme", "sf", "ca", "", "desc")
        assert key1 == key2

    def test_whitespace_normalized(self):
        """Extra whitespace collapsed to single space."""
        key1 = compute_content_key(
            "Software  Engineer", "  Acme  Corp  ", "SF", "CA", "", "desc"
        )
        key2 = compute_content_key(
            "Software Engineer", "Acme Corp", "SF", "CA", "", "desc"
        )
        assert key1 == key2

    def test_different_title_different_key(self):
        key1 = compute_content_key("Engineer", "Acme", "SF", "CA", "", "desc")
        key2 = compute_content_key("Manager", "Acme", "SF", "CA", "", "desc")
        assert key1 != key2

    def test_different_company_different_key(self):
        key1 = compute_content_key("Engineer", "Acme", "SF", "CA", "", "desc")
        key2 = compute_content_key("Engineer", "Beta", "SF", "CA", "", "desc")
        assert key1 != key2

    def test_different_city_different_key(self):
        key1 = compute_content_key("Engineer", "Acme", "SF", "CA", "", "desc")
        key2 = compute_content_key("Engineer", "Acme", "NYC", "CA", "", "desc")
        assert key1 != key2

    def test_different_state_different_key(self):
        key1 = compute_content_key("Engineer", "Acme", "SF", "CA", "", "desc")
        key2 = compute_content_key("Engineer", "Acme", "SF", "NY", "", "desc")
        assert key1 != key2

    def test_different_date_different_key(self):
        """Different date_posted = different key (re-post kept as separate entry)."""
        key1 = compute_content_key("Engineer", "Acme", "SF", "CA", "2026-01-01", "desc")
        key2 = compute_content_key("Engineer", "Acme", "SF", "CA", "2026-02-01", "desc")
        assert key1 != key2

    def test_different_description_different_key(self):
        key1 = compute_content_key("Engineer", "Acme", "SF", "CA", "", "A great role")
        key2 = compute_content_key("Engineer", "Acme", "SF", "CA", "", "Different role")
        assert key1 != key2

    def test_description_truncated_at_500(self):
        """Only first 500 chars of description are used."""
        shared = "x" * 500
        key1 = compute_content_key("E", "A", "", "", "", shared + "AAAA")
        key2 = compute_content_key("E", "A", "", "", "", shared + "BBBB")
        assert key1 == key2

    def test_empty_fields(self):
        """Empty strings and None-ish values handled gracefully."""
        key = compute_content_key("Engineer", "Acme", "", "", "", "")
        assert len(key) == 16

    def test_none_state(self):
        """None state treated as empty string."""
        key1 = compute_content_key("Engineer", "Acme", "SF", "", "", "desc")
        key2 = compute_content_key("Engineer", "Acme", "SF", "", "", "desc")
        assert key1 == key2


# ---------------------------------------------------------------------------
# Job.content_key computed field
# ---------------------------------------------------------------------------


class TestJobContentKey:
    def _make_job(self, **overrides):
        defaults = dict(
            source=Site.INDEED,
            source_id="jk123",
            url="https://example.com/job",
            title="Software Engineer",
            company="Amazon",
            location=Location(city="Seattle", state="WA"),
            description="Build scalable systems. " * 50,
            date_posted=date(2026, 1, 15),
        )
        defaults.update(overrides)
        return Job(**defaults)

    def test_computed_field_exists(self):
        job = self._make_job()
        assert hasattr(job, "content_key")
        assert len(job.content_key) == 16

    def test_same_content_same_key(self):
        """Two jobs with different source_id but same content get same content_key."""
        job1 = self._make_job(source_id="jk111")
        job2 = self._make_job(source_id="jk222")
        assert job1.dedup_key != job2.dedup_key  # source dedup differs
        assert job1.content_key == job2.content_key  # content same

    def test_different_content_different_key(self):
        job1 = self._make_job(title="Software Engineer")
        job2 = self._make_job(title="Data Scientist")
        assert job1.content_key != job2.content_key

    def test_uses_location_fields(self):
        job1 = self._make_job(location=Location(city="Seattle", state="WA"))
        job2 = self._make_job(location=Location(city="NYC", state="NY"))
        assert job1.content_key != job2.content_key

    def test_uses_date_posted(self):
        job1 = self._make_job(date_posted=date(2026, 1, 1))
        job2 = self._make_job(date_posted=date(2026, 2, 1))
        assert job1.content_key != job2.content_key

    def test_none_date_posted(self):
        job = self._make_job(date_posted=None)
        assert len(job.content_key) == 16


# ---------------------------------------------------------------------------
# DB: schema migration adds content_key column
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    d = JobDB(tmp_path / "test.db")
    yield d
    d.close()


def _make_db_job(
    source_id: str,
    *,
    source: str = "indeed",
    title: str = "Software Engineer",
    company: str = "Amazon",
    city: str = "Seattle",
    state: str = "WA",
    description: str = "Build scalable systems. " * 50,
    score: int = 50,
    status: str = "new",
    date_posted: date | None = date(2026, 1, 15),
) -> Job:
    return Job(
        source=Site(source),
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=title,
        company=company,
        location=Location(city=city, state=state),
        description=description,
        score=score,
        score_breakdown={"keyword": score},
        status=status,
        date_posted=date_posted,
    )


class TestContentKeyMigration:
    def test_content_key_column_exists(self, db):
        """New DBs have content_key column."""
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "content_key" in cols

    def test_content_key_index_exists(self, db):
        """content_key index is created."""
        indexes = {
            r[1]
            for r in db.conn.execute(
                "SELECT * FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_jobs_content_key" in indexes

    def test_migration_adds_column_to_old_db(self, tmp_path):
        """Simulate old DB without content_key, verify migration adds it."""
        db_path = tmp_path / "old.db"
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                city TEXT,
                state TEXT,
                country TEXT DEFAULT 'US',
                is_remote INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                job_type TEXT DEFAULT '[]',
                comp_min REAL,
                comp_max REAL,
                comp_currency TEXT DEFAULT 'USD',
                comp_interval TEXT,
                date_posted TEXT,
                date_scraped TEXT NOT NULL,
                score INTEGER DEFAULT 0,
                score_breakdown TEXT DEFAULT '{}',
                status TEXT DEFAULT 'new',
                notes TEXT DEFAULT '',
                applied_date TEXT,
                search_term TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                site TEXT NOT NULL,
                search_term TEXT NOT NULL,
                location TEXT NOT NULL,
                jobs_found INTEGER DEFAULT 0,
                jobs_new INTEGER DEFAULT 0,
                error TEXT
            );
            """
        )
        conn.close()

        # Opening JobDB should run migration
        db = JobDB(db_path)
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "content_key" in cols
        db.close()


# ---------------------------------------------------------------------------
# DB: upsert_job with content_key dedup
# ---------------------------------------------------------------------------


class TestUpsertContentDedup:
    def test_insert_stores_content_key(self, db):
        """Inserted job gets content_key stored in DB."""
        job = _make_db_job("jk100")
        is_new, row_id = db.upsert_job(job)
        assert is_new is True

        row = db.conn.execute(
            "SELECT content_key FROM jobs WHERE id = ?", (row_id,)
        ).fetchone()
        assert row["content_key"] == job.content_key

    def test_exact_dedup_still_works(self, db):
        """Same source+source_id still caught by dedup_key (existing behavior)."""
        job1 = _make_db_job("jk200")
        is_new1, id1 = db.upsert_job(job1)
        assert is_new1 is True

        job2 = _make_db_job("jk200")  # same source_id
        is_new2, id2 = db.upsert_job(job2)
        assert is_new2 is False
        assert id2 == id1

    def test_content_dedup_catches_same_content_different_source_id(self, db):
        """Different source_id but identical content -> not inserted, returns existing."""
        job1 = _make_db_job("jk300")
        is_new1, id1 = db.upsert_job(job1)
        assert is_new1 is True

        job2 = _make_db_job("jk301")  # different source_id, same content
        is_new2, id2 = db.upsert_job(job2)
        assert is_new2 is False
        assert id2 == id1

    def test_content_dedup_updates_score_if_higher(self, db):
        """When content dupe has higher score, keeper's score is updated."""
        job1 = _make_db_job("jk400", score=30)
        _, id1 = db.upsert_job(job1)

        job2 = _make_db_job("jk401", score=80)  # higher score, same content
        _, id2 = db.upsert_job(job2)
        assert id2 == id1

        fetched = db.get_job(id1)
        assert fetched.score == 80

    def test_content_dedup_keeps_lower_score_if_existing_higher(self, db):
        """When content dupe has lower score, keeper's score is unchanged."""
        job1 = _make_db_job("jk500", score=80)
        _, id1 = db.upsert_job(job1)

        job2 = _make_db_job("jk501", score=30)  # lower score
        _, id2 = db.upsert_job(job2)
        assert id2 == id1

        fetched = db.get_job(id1)
        assert fetched.score == 80

    def test_content_dedup_skipped_for_short_description(self, db):
        """Short descriptions (<= 100 chars) skip content dedup to avoid false positives."""
        job1 = _make_db_job("jk600", description="Short desc")
        is_new1, _ = db.upsert_job(job1)
        assert is_new1 is True

        job2 = _make_db_job("jk601", description="Short desc")
        is_new2, _ = db.upsert_job(job2)
        assert is_new2 is True  # not caught — description too short

    def test_content_dedup_allows_different_content(self, db):
        """Jobs with different content go through as new entries."""
        job1 = _make_db_job("jk700", title="Software Engineer")
        is_new1, _ = db.upsert_job(job1)
        assert is_new1 is True

        job2 = _make_db_job("jk701", title="Data Scientist")
        is_new2, _ = db.upsert_job(job2)
        assert is_new2 is True  # different content = new entry

    def test_content_dedup_respects_different_date_posted(self, db):
        """Same title+company but different date_posted = different entry (re-post)."""
        job1 = _make_db_job("jk800", date_posted=date(2026, 1, 1))
        is_new1, _ = db.upsert_job(job1)
        assert is_new1 is True

        job2 = _make_db_job("jk801", date_posted=date(2026, 2, 1))
        is_new2, _ = db.upsert_job(job2)
        assert is_new2 is True  # different date = re-post, kept


# ---------------------------------------------------------------------------
# DB: backfill_content_keys
# ---------------------------------------------------------------------------


class TestBackfillContentKeys:
    def test_backfills_null_content_keys(self, db):
        """backfill_content_keys fills in NULL content_key rows."""
        # Insert a job normally (content_key populated)
        job = _make_db_job("bf1")
        _, row_id = db.upsert_job(job)

        # Manually null out content_key to simulate old row
        db.conn.execute("UPDATE jobs SET content_key = NULL WHERE id = ?", (row_id,))
        db.conn.commit()

        count = db.backfill_content_keys()
        assert count == 1

        row = db.conn.execute(
            "SELECT content_key FROM jobs WHERE id = ?", (row_id,)
        ).fetchone()
        assert row["content_key"] is not None
        assert len(row["content_key"]) == 16

    def test_backfill_skips_already_populated(self, db):
        """Rows that already have content_key are not touched."""
        job = _make_db_job("bf2")
        db.upsert_job(job)

        count = db.backfill_content_keys()
        assert count == 0

    def test_backfill_returns_count(self, db):
        """Returns number of rows updated."""
        for i in range(3):
            db.upsert_job(_make_db_job(f"bf3_{i}", title=f"Role {i}"))

        db.conn.execute("UPDATE jobs SET content_key = NULL")
        db.conn.commit()

        count = db.backfill_content_keys()
        assert count == 3


# ---------------------------------------------------------------------------
# DB: find_duplicates
# ---------------------------------------------------------------------------


class TestFindDuplicates:
    def test_finds_duplicate_groups(self, db):
        """Groups by content_key where count > 1."""
        # Two jobs with same content but different source_id
        # Insert directly to bypass content dedup in upsert
        job1 = _make_db_job("fd1")
        job2 = _make_db_job("fd2")  # same content, different source_id

        # Insert first normally
        db.upsert_job(job1)
        # Force-insert second to bypass content check
        db.conn.execute(
            """INSERT INTO jobs (
                dedup_key, source, source_id, url, title, company,
                city, state, country, is_remote, description, job_type,
                comp_min, comp_max, comp_currency, comp_interval,
                date_posted, date_scraped, score, score_breakdown, status, notes,
                search_term, content_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job2.dedup_key,
                job2.source.value,
                job2.source_id,
                job2.url,
                job2.title,
                job2.company,
                job2.location.city,
                job2.location.state,
                job2.location.country,
                int(job2.location.is_remote),
                job2.description,
                "[]",
                None,
                None,
                "USD",
                None,
                job2.date_posted.isoformat() if job2.date_posted else None,
                job2.date_scraped.isoformat(),
                job2.score,
                "{}",
                job2.status,
                "",
                None,
                job2.content_key,
            ),
        )
        db.conn.commit()

        groups = db.find_duplicates()
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_no_duplicates(self, db):
        """No groups returned when all content_keys are unique."""
        db.upsert_job(_make_db_job("fd3", title="Engineer"))
        db.upsert_job(_make_db_job("fd4", title="Manager"))

        groups = db.find_duplicates()
        assert len(groups) == 0


# ---------------------------------------------------------------------------
# DB: _pick_keeper
# ---------------------------------------------------------------------------


class TestPickKeeper:
    def test_prefers_applied_status(self, db):
        """Applied job is kept over new/filtered."""
        rows = [
            {
                "id": 1,
                "status": "new",
                "score": 90,
                "date_scraped": "2026-01-01T00:00:00",
            },
            {
                "id": 2,
                "status": "applied",
                "score": 50,
                "date_scraped": "2026-01-02T00:00:00",
            },
            {
                "id": 3,
                "status": "filtered",
                "score": 80,
                "date_scraped": "2026-01-03T00:00:00",
            },
        ]
        keeper_id = JobDB._pick_keeper(rows)
        assert keeper_id == 2

    def test_prefers_higher_score_when_same_status(self, db):
        """Among same-status rows, highest score wins."""
        rows = [
            {
                "id": 1,
                "status": "new",
                "score": 30,
                "date_scraped": "2026-01-01T00:00:00",
            },
            {
                "id": 2,
                "status": "new",
                "score": 90,
                "date_scraped": "2026-01-02T00:00:00",
            },
            {
                "id": 3,
                "status": "new",
                "score": 60,
                "date_scraped": "2026-01-03T00:00:00",
            },
        ]
        keeper_id = JobDB._pick_keeper(rows)
        assert keeper_id == 2

    def test_prefers_earliest_scraped_as_tiebreaker(self, db):
        """Same status + same score -> earliest date_scraped wins."""
        rows = [
            {
                "id": 1,
                "status": "new",
                "score": 50,
                "date_scraped": "2026-01-03T00:00:00",
            },
            {
                "id": 2,
                "status": "new",
                "score": 50,
                "date_scraped": "2026-01-01T00:00:00",
            },
            {
                "id": 3,
                "status": "new",
                "score": 50,
                "date_scraped": "2026-01-02T00:00:00",
            },
        ]
        keeper_id = JobDB._pick_keeper(rows)
        assert keeper_id == 2

    def test_status_priority_order(self, db):
        """applied > new > filtered > rejected."""
        rows = [
            {
                "id": 1,
                "status": "rejected",
                "score": 90,
                "date_scraped": "2026-01-01T00:00:00",
            },
            {
                "id": 2,
                "status": "filtered",
                "score": 90,
                "date_scraped": "2026-01-01T00:00:00",
            },
            {
                "id": 3,
                "status": "new",
                "score": 50,
                "date_scraped": "2026-01-01T00:00:00",
            },
        ]
        keeper_id = JobDB._pick_keeper(rows)
        assert keeper_id == 3  # 'new' beats 'filtered' and 'rejected'


# ---------------------------------------------------------------------------
# DB: deduplicate
# ---------------------------------------------------------------------------


class TestDeduplicate:
    def _force_insert(self, db, job):
        """Insert a job bypassing content dedup (to create deliberate duplicates)."""
        import json

        comp = job.compensation
        db.conn.execute(
            """INSERT INTO jobs (
                dedup_key, source, source_id, url, title, company,
                city, state, country, is_remote, description, job_type,
                comp_min, comp_max, comp_currency, comp_interval,
                date_posted, date_scraped, score, score_breakdown, status, notes,
                search_term, content_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.dedup_key,
                job.source.value,
                job.source_id,
                job.url,
                job.title,
                job.company,
                job.location.city,
                job.location.state,
                job.location.country,
                int(job.location.is_remote),
                job.description[:50000],
                json.dumps([jt.value for jt in job.job_type]),
                comp.min_amount if comp else None,
                comp.max_amount if comp else None,
                comp.currency if comp else "USD",
                comp.interval.value if comp and comp.interval else None,
                job.date_posted.isoformat() if job.date_posted else None,
                job.date_scraped.isoformat(),
                job.score,
                json.dumps(job.score_breakdown),
                job.status,
                job.notes,
                job.search_term,
                job.content_key,
            ),
        )
        db.conn.commit()

    def test_dry_run_does_not_delete(self, db):
        """dry_run=True reports but doesn't delete."""
        db.upsert_job(_make_db_job("dd1", score=30))
        self._force_insert(db, _make_db_job("dd2", score=80))

        result = db.deduplicate(dry_run=True)
        assert result["groups"] == 1
        assert result["removed"] == 1

        # Still 2 rows in DB
        count = db.conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
        assert count == 2

    def test_real_run_deletes_dupes(self, db):
        """dry_run=False actually removes duplicates."""
        db.upsert_job(_make_db_job("dd3", score=30))
        self._force_insert(db, _make_db_job("dd4", score=80))

        result = db.deduplicate(dry_run=False)
        assert result["groups"] == 1
        assert result["removed"] == 1
        assert result["kept"] == 1

        count = db.conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
        assert count == 1

    def test_keeps_highest_score(self, db):
        """The row with highest score is kept."""
        db.upsert_job(_make_db_job("dd5", score=30))
        self._force_insert(db, _make_db_job("dd6", score=80))

        db.deduplicate(dry_run=False)

        remaining = db.conn.execute("SELECT score FROM jobs").fetchone()
        assert remaining["score"] == 80

    def test_keeps_applied_over_high_score(self, db):
        """Applied status beats higher score."""
        _, id1 = db.upsert_job(_make_db_job("dd7", score=30))
        db.update_status(id1, "applied")
        self._force_insert(db, _make_db_job("dd8", score=90))

        db.deduplicate(dry_run=False)

        remaining = db.conn.execute("SELECT status, score FROM jobs").fetchone()
        assert remaining["status"] == "applied"
        assert remaining["score"] == 30

    def test_no_dupes_no_changes(self, db):
        """When there are no duplicates, nothing is deleted."""
        db.upsert_job(_make_db_job("dd9", title="Engineer"))
        db.upsert_job(_make_db_job("dd10", title="Manager"))

        result = db.deduplicate(dry_run=False)
        assert result["groups"] == 0
        assert result["removed"] == 0

        count = db.conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
        assert count == 2

    def test_multiple_groups(self, db):
        """Multiple duplicate groups are all handled."""
        # Group 1: same content (Engineer at Amazon)
        db.upsert_job(_make_db_job("mg1", title="Engineer", company="Amazon"))
        self._force_insert(db, _make_db_job("mg2", title="Engineer", company="Amazon"))
        self._force_insert(db, _make_db_job("mg3", title="Engineer", company="Amazon"))

        # Group 2: same content (Manager at Google)
        db.upsert_job(_make_db_job("mg4", title="Manager", company="Google"))
        self._force_insert(db, _make_db_job("mg5", title="Manager", company="Google"))

        # Unique job
        db.upsert_job(_make_db_job("mg6", title="Designer", company="Apple"))

        result = db.deduplicate(dry_run=False)
        assert result["groups"] == 2
        assert result["removed"] == 3  # 2 from group1 + 1 from group2

        count = db.conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
        assert count == 3  # 1 keeper per group + 1 unique


# ---------------------------------------------------------------------------
# CLI: dedup command
# ---------------------------------------------------------------------------


class TestDedupCLI:
    def test_dry_run(self, tmp_path, monkeypatch):
        """dedup --dry-run reports but doesn't delete."""
        from typer.testing import CliRunner
        from job_scout.cli import app

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        db.upsert_job(_make_db_job("cli1", score=30))
        # Force insert duplicate

        job2 = _make_db_job("cli2", score=80)
        db.conn.execute(
            """INSERT INTO jobs (
                dedup_key, source, source_id, url, title, company,
                city, state, country, is_remote, description, job_type,
                date_posted, date_scraped, score, score_breakdown, status, notes,
                search_term, content_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job2.dedup_key,
                job2.source.value,
                job2.source_id,
                job2.url,
                job2.title,
                job2.company,
                job2.location.city,
                job2.location.state,
                job2.location.country,
                int(job2.location.is_remote),
                job2.description,
                "[]",
                job2.date_posted.isoformat() if job2.date_posted else None,
                job2.date_scraped.isoformat(),
                job2.score,
                "{}",
                job2.status,
                "",
                None,
                job2.content_key,
            ),
        )
        db.conn.commit()
        db.close()

        # Monkeypatch config to use our test DB
        monkeypatch.setattr(
            "job_scout.cli._get_db",
            lambda cfg=None: JobDB(db_path),
        )
        monkeypatch.setattr(
            "job_scout.cli._get_config",
            lambda: type("C", (), {"_config_path": None, "db_path": db_path})(),
        )

        runner = CliRunner()
        result = runner.invoke(app, ["dedup", "--dry-run"])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

        # Verify nothing was deleted
        db = JobDB(db_path)
        count = db.conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
        assert count == 2
        db.close()

    def test_backfill_only(self, tmp_path, monkeypatch):
        """dedup --backfill-only fills content_keys but doesn't dedup."""
        from typer.testing import CliRunner
        from job_scout.cli import app

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        db.upsert_job(_make_db_job("cli3"))
        # Null out content_key
        db.conn.execute("UPDATE jobs SET content_key = NULL")
        db.conn.commit()
        db.close()

        monkeypatch.setattr(
            "job_scout.cli._get_db",
            lambda cfg=None: JobDB(db_path),
        )
        monkeypatch.setattr(
            "job_scout.cli._get_config",
            lambda: type("C", (), {"_config_path": None, "db_path": db_path})(),
        )

        runner = CliRunner()
        result = runner.invoke(app, ["dedup", "--backfill-only"])
        assert result.exit_code == 0
        assert "backfill" in result.output.lower() or "Backfill" in result.output

        # Verify content_key was filled
        db = JobDB(db_path)
        row = db.conn.execute("SELECT content_key FROM jobs").fetchone()
        assert row["content_key"] is not None
        db.close()
