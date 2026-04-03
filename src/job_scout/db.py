"""SQLite persistence layer for jobs and scrape runs."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from job_scout.models import (
    Compensation,
    CompInterval,
    Job,
    JobType,
    Location,
    ScrapeRun,
    Site,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key       TEXT UNIQUE NOT NULL,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    city            TEXT,
    state           TEXT,
    country         TEXT DEFAULT 'US',
    is_remote       INTEGER DEFAULT 0,
    description     TEXT DEFAULT '',
    job_type        TEXT DEFAULT '[]',
    comp_min        REAL,
    comp_max        REAL,
    comp_currency   TEXT DEFAULT 'USD',
    comp_interval   TEXT,
    date_posted     TEXT,
    date_scraped    TEXT NOT NULL,
    score           INTEGER DEFAULT 0,
    score_breakdown TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'new',
    notes           TEXT DEFAULT '',
    applied_date    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_date_posted ON jobs(date_posted DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    site          TEXT NOT NULL,
    search_term   TEXT NOT NULL,
    location      TEXT NOT NULL,
    jobs_found    INTEGER DEFAULT 0,
    jobs_new      INTEGER DEFAULT 0,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON scrape_runs(started_at DESC);
"""


class JobDB:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        self.conn.close()

    # --- Job CRUD ---

    def upsert_job(self, job: Job) -> tuple[bool, int]:
        """Insert a job. Returns (is_new, row_id)."""
        cur = self.conn.execute(
            "SELECT id FROM jobs WHERE dedup_key = ?", (job.dedup_key,)
        )
        existing = cur.fetchone()
        if existing:
            # Update score if recalculated
            self.conn.execute(
                "UPDATE jobs SET score = ?, score_breakdown = ?, updated_at = datetime('now') WHERE id = ?",
                (job.score, json.dumps(job.score_breakdown), existing["id"]),
            )
            self.conn.commit()
            return False, existing["id"]

        comp = job.compensation
        cur = self.conn.execute(
            """INSERT INTO jobs (
                dedup_key, source, source_id, url, title, company,
                city, state, country, is_remote, description, job_type,
                comp_min, comp_max, comp_currency, comp_interval,
                date_posted, date_scraped, score, score_breakdown, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                job.description[:50000],  # cap at 50KB
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
            ),
        )
        self.conn.commit()
        return True, cur.lastrowid

    def job_exists(self, dedup_key: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM jobs WHERE dedup_key = ?", (dedup_key,)
        )
        return cur.fetchone() is not None

    def get_jobs(
        self,
        *,
        status: str | None = None,
        min_score: int | None = None,
        company: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        clauses = []
        params: list = []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        if min_score is not None:
            clauses.append("score >= ?")
            params.append(min_score)
        if company:
            clauses.append("company LIKE ?")
            params.append(f"%{company}%")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM jobs {where} ORDER BY score DESC, date_posted DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_job(self, job_id: int) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def update_status(self, job_id: int, status: str, notes: str = "") -> None:
        updates = ["status = ?", "updated_at = datetime('now')"]
        params: list = [status]
        if notes:
            updates.append("notes = ?")
            params.append(notes)
        if status == "applied":
            updates.append("applied_date = ?")
            params.append(date.today().isoformat())
        params.append(job_id)
        self.conn.execute(
            f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?", params
        )
        self.conn.commit()

    def mark_applied(self, job_id: int, notes: str = "") -> None:
        self.update_status(job_id, "applied", notes)

    def get_stats(self) -> dict:
        stats = {}
        # By status
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        stats["by_status"] = {r["status"]: r["cnt"] for r in rows}
        stats["total"] = sum(stats["by_status"].values())

        # By source
        rows = self.conn.execute(
            "SELECT source, COUNT(*) as cnt FROM jobs GROUP BY source"
        ).fetchall()
        stats["by_source"] = {r["source"]: r["cnt"] for r in rows}

        # Score distribution
        rows = self.conn.execute(
            """SELECT
                CASE
                    WHEN score >= 80 THEN 'excellent (80+)'
                    WHEN score >= 55 THEN 'good (55-79)'
                    WHEN score >= 30 THEN 'review (30-54)'
                    ELSE 'low (<30)'
                END as tier,
                COUNT(*) as cnt
            FROM jobs WHERE score > 0
            GROUP BY tier"""
        ).fetchall()
        stats["score_distribution"] = {r["tier"]: r["cnt"] for r in rows}

        # Recent runs
        rows = self.conn.execute(
            "SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 5"
        ).fetchall()
        stats["recent_runs"] = [dict(r) for r in rows]

        return stats

    # --- Scrape runs ---

    def record_run(self, run: ScrapeRun) -> int:
        cur = self.conn.execute(
            "INSERT INTO scrape_runs (started_at, site, search_term, location) VALUES (?, ?, ?, ?)",
            (run.started_at.isoformat(), run.site.value, run.search_term, run.location),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(
        self, run_id: int, jobs_found: int, jobs_new: int, error: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE scrape_runs SET finished_at = ?, jobs_found = ?, jobs_new = ?, error = ? WHERE id = ?",
            (datetime.now().isoformat(), jobs_found, jobs_new, error, run_id),
        )
        self.conn.commit()

    # --- Helpers ---

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        comp = None
        if row["comp_min"] is not None:
            comp = Compensation(
                min_amount=row["comp_min"],
                max_amount=row["comp_max"],
                currency=row["comp_currency"] or "USD",
                interval=CompInterval(row["comp_interval"]) if row["comp_interval"] else None,
            )

        return Job(
            id=row["id"],
            source=Site(row["source"]),
            source_id=row["source_id"],
            url=row["url"],
            title=row["title"],
            company=row["company"],
            location=Location(
                city=row["city"],
                state=row["state"],
                country=row["country"],
                is_remote=bool(row["is_remote"]),
            ),
            description=row["description"],
            job_type=[JobType(jt) for jt in json.loads(row["job_type"])],
            compensation=comp,
            date_posted=date.fromisoformat(row["date_posted"]) if row["date_posted"] else None,
            date_scraped=datetime.fromisoformat(row["date_scraped"]),
            score=row["score"],
            score_breakdown=json.loads(row["score_breakdown"]),
            status=row["status"],
            notes=row["notes"] or "",
            applied_date=date.fromisoformat(row["applied_date"]) if row["applied_date"] else None,
        )
