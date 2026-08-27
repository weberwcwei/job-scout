"""SQLite persistence layer for jobs and scrape runs."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from job_scout.models import (
    ATSJob,
    ATSProvider,
    CanonicalJob,
    Company,
    Compensation,
    CompInterval,
    Job,
    JobType,
    Location,
    ScrapeRun,
    Site,
    compute_content_key,
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
    last_seen_at    TEXT NOT NULL,
    score           INTEGER DEFAULT 0,
    score_breakdown TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'new',
    notes           TEXT DEFAULT '',
    applied_date    TEXT,
    search_term     TEXT,
    content_key     TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_date_posted ON jobs(date_posted DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_date_scraped ON jobs(date_scraped DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_search_term ON jobs(search_term);

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

CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    domain          TEXT,
    careers_url     TEXT,
    ats             TEXT DEFAULT 'unknown',
    ats_slug        TEXT,
    discovered_from TEXT DEFAULT '[]',
    last_verified_at TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_companies_ats ON companies(ats);
CREATE INDEX IF NOT EXISTS idx_companies_slug ON companies(ats_slug);

CREATE TABLE IF NOT EXISTS ats_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key       TEXT UNIQUE NOT NULL,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    company_id      INTEGER,
    company         TEXT NOT NULL,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    apply_url       TEXT,
    location_text   TEXT,
    city            TEXT,
    state           TEXT,
    country         TEXT,
    is_remote       INTEGER DEFAULT 0,
    hybrid          INTEGER DEFAULT 0,
    employment_type TEXT,
    description_html TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    salary_min      REAL,
    salary_max      REAL,
    currency        TEXT DEFAULT 'AUD',
    posted_at       TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    closed_at       TEXT,
    status          TEXT DEFAULT 'open',
    repost          INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_ats_jobs_source ON ats_jobs(source);
CREATE INDEX IF NOT EXISTS idx_ats_jobs_company ON ats_jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_ats_jobs_status ON ats_jobs(status);
CREATE INDEX IF NOT EXISTS idx_ats_jobs_last_seen ON ats_jobs(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS canonical_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key   TEXT UNIQUE NOT NULL,
    company_id      INTEGER,
    company         TEXT NOT NULL,
    title           TEXT NOT NULL,
    location_text   TEXT,
    city            TEXT,
    state           TEXT,
    country         TEXT,
    is_remote       INTEGER DEFAULT 0,
    hybrid          INTEGER DEFAULT 0,
    employment_type TEXT,
    salary_min      REAL,
    salary_max      REAL,
    currency        TEXT DEFAULT 'AUD',
    description_html TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    url             TEXT DEFAULT '',
    apply_url       TEXT,
    posted_at       TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    closed_at       TEXT,
    status          TEXT DEFAULT 'open',
    repost          INTEGER DEFAULT 0,
    source_ids      TEXT DEFAULT '[]',
    score           INTEGER DEFAULT 0,
    score_breakdown TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_canonical_company ON canonical_jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_canonical_status ON canonical_jobs(status);
CREATE INDEX IF NOT EXISTS idx_canonical_last_seen ON canonical_jobs(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_score ON canonical_jobs(score DESC);
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
        self._migrate_canonical_columns()
        self.conn.executescript(SCHEMA_SQL)
        self._migrate()

    def _migrate_canonical_columns(self) -> None:
        """Add score columns to a pre-existing canonical_jobs table.

        Runs before SCHEMA_SQL so the `CREATE INDEX ... score` can't fail on an
        old table that predates the score columns.
        """
        names = [
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='canonical_jobs'"
            ).fetchall()
        ]
        if not names:
            return
        cols = {
            r[1]
            for r in self.conn.execute("PRAGMA table_info(canonical_jobs)").fetchall()
        }
        if "score" not in cols:
            self.conn.execute("ALTER TABLE canonical_jobs ADD COLUMN score INTEGER DEFAULT 0")
        if "score_breakdown" not in cols:
            self.conn.execute(
                "ALTER TABLE canonical_jobs ADD COLUMN score_breakdown TEXT DEFAULT '{}'"
            )
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns that may be missing in databases created before this version."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "search_term" not in cols:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN search_term TEXT")
            self.conn.commit()
        if "content_key" not in cols:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN content_key TEXT")
            self.conn.commit()
        if "last_seen_at" not in cols:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN last_seen_at TEXT")
            self.conn.execute(
                "UPDATE jobs SET last_seen_at = date_scraped WHERE last_seen_at IS NULL"
            )
            self.conn.commit()
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_content_key ON jobs(content_key)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at ON jobs(last_seen_at DESC)"
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- Job CRUD ---

    def upsert_job(self, job: Job) -> tuple[bool, int]:
        """Insert a job. Returns (is_new, row_id)."""
        cur = self.conn.execute(
            "SELECT id, status FROM jobs WHERE dedup_key = ?", (job.dedup_key,)
        )
        existing = cur.fetchone()
        if existing:
            old_status = existing["status"]
            new_status = (
                job.status
                if old_status in ("new", "filtered", "expired", "low_score")
                else old_status
            )
            if old_status == "rejected":
                self.conn.execute(
                    "UPDATE jobs SET last_seen_at = ?, updated_at = datetime('now') WHERE id = ?",
                    (job.date_scraped.isoformat(), existing["id"]),
                )
            else:
                self.conn.execute(
                    "UPDATE jobs SET score = ?, score_breakdown = ?, status = ?, last_seen_at = ?, updated_at = datetime('now') WHERE id = ?",
                    (
                        job.score,
                        json.dumps(job.score_breakdown),
                        new_status,
                        job.date_scraped.isoformat(),
                        existing["id"],
                    ),
                )
            self.conn.commit()
            return False, existing["id"]

        # Content-based soft dedup (skip for short descriptions)
        if job.description and len(job.description) > 100:
            cur = self.conn.execute(
                "SELECT id, status, score FROM jobs WHERE content_key = ?",
                (job.content_key,),
            )
            content_match = cur.fetchone()
            if content_match:
                if content_match["status"] == "expired":
                    self.conn.execute(
                        "UPDATE jobs SET score = ?, score_breakdown = ?, status = ?, last_seen_at = ?, updated_at = datetime('now') WHERE id = ?",
                        (
                            job.score,
                            json.dumps(job.score_breakdown),
                            job.status,
                            job.date_scraped.isoformat(),
                            content_match["id"],
                        ),
                    )
                elif (
                    content_match["status"] != "rejected"
                    and job.score > content_match["score"]
                ):
                    self.conn.execute(
                        "UPDATE jobs SET score = ?, score_breakdown = ?, last_seen_at = ?, updated_at = datetime('now') WHERE id = ?",
                        (
                            job.score,
                            json.dumps(job.score_breakdown),
                            job.date_scraped.isoformat(),
                            content_match["id"],
                        ),
                    )
                else:
                    self.conn.execute(
                        "UPDATE jobs SET last_seen_at = ?, updated_at = datetime('now') WHERE id = ?",
                        (job.date_scraped.isoformat(), content_match["id"]),
                    )
                self.conn.commit()
                return False, content_match["id"]

        comp = job.compensation
        cur = self.conn.execute(
            """INSERT INTO jobs (
                dedup_key, source, source_id, url, title, company,
                city, state, country, is_remote, description, job_type,
                comp_min, comp_max, comp_currency, comp_interval,
                date_posted, date_scraped, last_seen_at, score, score_breakdown, status, notes,
                search_term, content_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                job.date_scraped.isoformat(),
                job.score,
                json.dumps(job.score_breakdown),
                job.status,
                job.notes,
                job.search_term,
                job.content_key,
            ),
        )
        self.conn.commit()
        return True, cur.lastrowid

    def job_exists(self, dedup_key: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM jobs WHERE dedup_key = ?", (dedup_key,))
        return cur.fetchone() is not None

    def get_jobs(
        self,
        *,
        status: str | None = None,
        min_score: int | None = None,
        company: str | None = None,
        source: str | None = None,
        since: datetime | None = None,
        limit: int | None = 50,
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
        if source:
            clauses.append("source = ?")
            params.append(source)
        if since is not None:
            clauses.append("date_scraped >= ?")
            params.append(since.isoformat())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM jobs {where} ORDER BY score DESC, date_posted DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
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
        if status in ("applied", "interview", "offer"):
            updates.append("applied_date = COALESCE(applied_date, ?)")
            params.append(date.today().isoformat())
        if status == "rejected":
            # Demote so rejected jobs sink out of score-ordered views and can't
            # be re-alerted. Stash the original score for reference.
            updates.append("score = 0")
            updates.append(
                "score_breakdown = json_set("
                "CASE WHEN json_valid(score_breakdown) THEN score_breakdown ELSE '{}' END, "
                "'$.pre_reject_score', COALESCE("
                "json_extract(CASE WHEN json_valid(score_breakdown) THEN score_breakdown ELSE '{}' END, '$.pre_reject_score'), "
                "score))"
            )
        params.append(job_id)
        self.conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()

    def mark_applied(self, job_id: int, notes: str = "") -> None:
        self.update_status(job_id, "applied", notes)

    def expire_old_jobs(self, days: int) -> int:
        """Mark `new`/`filtered` jobs unseen for `days` as expired.

        User-tracked statuses (applied/interview/offer/rejected) are never
        touched — they are history, not inventory.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cur = self.conn.execute(
            """UPDATE jobs SET status = 'expired', updated_at = datetime('now')
            WHERE status IN ('new', 'filtered', 'low_score') AND last_seen_at < ?""",
            (cutoff,),
        )
        self.conn.commit()
        return cur.rowcount

    def get_recent_jobs(self, days: int = 14, limit: int = 50) -> list[Job]:
        """Return recent jobs + any with active status, for bot LLM context."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            """SELECT * FROM jobs
            WHERE date_scraped >= ?
               OR status IN ('applied', 'interview', 'offer')
            ORDER BY score DESC, date_posted DESC
            LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

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

        # Zero-result runs (no error but jobs_found=0) in last 7 days
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        zero_count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM scrape_runs WHERE jobs_found = 0 AND error IS NULL AND started_at >= ?",
            (cutoff,),
        ).fetchone()["cnt"]
        rows = self.conn.execute(
            """SELECT site, search_term, location, started_at
            FROM scrape_runs
            WHERE jobs_found = 0
              AND error IS NULL
              AND started_at >= ?
            ORDER BY started_at DESC
            LIMIT 10""",
            (cutoff,),
        ).fetchall()
        stats["zero_result_runs"] = {
            "count": zero_count,
            "recent": [dict(r) for r in rows],
        }

        # Search term performance (exclude filtered/dealbreaker jobs for meaningful avg)
        rows = self.conn.execute(
            """SELECT
                search_term,
                COUNT(*) as cnt,
                ROUND(AVG(score), 1) as avg_score
            FROM jobs
            WHERE search_term IS NOT NULL
              AND status != 'filtered'
            GROUP BY search_term
            ORDER BY avg_score DESC"""
        ).fetchall()
        stats["by_search_term"] = [
            {
                "search_term": r["search_term"],
                "count": r["cnt"],
                "avg_score": r["avg_score"],
            }
            for r in rows
        ]

        return stats

    def get_alert_stats(self, since_hours: int = 24, score_threshold: int = 55) -> dict:
        """Stats for digest/report: total unreviewed, scraped in window, high/medium counts."""
        cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()

        total_new = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE status = 'new'"
        ).fetchone()["cnt"]

        scraped_24h = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE date_scraped >= ?", (cutoff,)
        ).fetchone()["cnt"]

        high_count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE date_scraped >= ? AND score >= ?",
            (cutoff, score_threshold),
        ).fetchone()["cnt"]

        medium_count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE date_scraped >= ? AND score >= 40 AND score < ?",
            (cutoff, score_threshold),
        ).fetchone()["cnt"]

        return {
            "total_new": total_new,
            "scraped_24h": scraped_24h,
            "high_count": high_count,
            "medium_count": medium_count,
        }

    def get_daily_trend(self, days: int, score_threshold: int) -> list[dict]:
        """Per-day breakdown for the last N days: total scraped, high, medium counts."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.conn.execute(
            """SELECT
                DATE(date_scraped) as day,
                COUNT(*) as total,
                SUM(CASE WHEN score >= ? THEN 1 ELSE 0 END) as high,
                SUM(CASE WHEN score >= 40 AND score < ? THEN 1 ELSE 0 END) as medium
            FROM jobs
            WHERE DATE(date_scraped) >= ?
            GROUP BY DATE(date_scraped)
            ORDER BY day DESC""",
            (score_threshold, score_threshold, cutoff),
        ).fetchall()
        return [
            {
                "date": r["day"],
                "total": r["total"],
                "high": r["high"],
                "medium": r["medium"],
            }
            for r in rows
        ]

    def batch_update_scores(self, updates: list[tuple[int, int, dict]]) -> None:
        """Batch update job scores. Each tuple: (row_id, score, breakdown)."""
        self.conn.execute("BEGIN")
        try:
            for row_id, score, breakdown in updates:
                self.conn.execute(
                    "UPDATE jobs SET score = ?, score_breakdown = ?, updated_at = datetime('now') WHERE id = ?",
                    (score, json.dumps(breakdown), row_id),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

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

    # --- Content dedup ---

    def backfill_content_keys(self) -> int:
        """Compute content_key for rows where it is NULL. Returns count updated.

        Normalizes location via the Location model before hashing so
        backfilled keys match keys generated at insert time.
        """
        rows = self.conn.execute(
            "SELECT id, title, company, city, state, country, is_remote, "
            "date_posted, description FROM jobs WHERE content_key IS NULL"
        ).fetchall()
        if not rows:
            return 0
        self.conn.execute("BEGIN")
        try:
            for row in rows:
                loc = Location(
                    city=row["city"],
                    state=row["state"],
                    country=row["country"],
                    is_remote=bool(row["is_remote"]),
                )
                key = compute_content_key(
                    row["title"],
                    row["company"],
                    loc.city or "",
                    loc.state or "",
                    row["date_posted"] or "",
                    row["description"] or "",
                )
                self.conn.execute(
                    "UPDATE jobs SET content_key = ? WHERE id = ?",
                    (key, row["id"]),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return len(rows)

    def find_duplicates(self) -> list[list[dict]]:
        """Return groups of rows sharing a content_key (count > 1)."""
        dup_keys = self.conn.execute(
            "SELECT content_key, COUNT(*) as cnt FROM jobs "
            "WHERE content_key IS NOT NULL "
            "GROUP BY content_key HAVING cnt > 1"
        ).fetchall()
        groups = []
        for row in dup_keys:
            members = self.conn.execute(
                "SELECT id, status, score, date_scraped FROM jobs WHERE content_key = ?",
                (row["content_key"],),
            ).fetchall()
            groups.append([dict(m) for m in members])
        return groups

    def deduplicate(self, *, dry_run: bool = False) -> dict:
        """Remove content-duplicate rows, keeping the best per group.

        Returns dict with keys: groups, removed, kept.
        """
        groups = self.find_duplicates()
        total_removed = 0
        for group in groups:
            keeper_id = self._pick_keeper(group)
            remove_ids = [m["id"] for m in group if m["id"] != keeper_id]
            total_removed += len(remove_ids)
            if not dry_run:
                placeholders = ",".join("?" * len(remove_ids))
                self.conn.execute(
                    f"DELETE FROM jobs WHERE id IN ({placeholders})", remove_ids
                )
        if not dry_run and groups:
            self.conn.commit()
        return {
            "groups": len(groups),
            "removed": total_removed,
            "kept": len(groups),
        }

    @staticmethod
    def _pick_keeper(rows: list[dict]) -> int:
        """Pick the best row to keep. Priority: applied > new > filtered > rejected,
        then highest score, then earliest date_scraped."""
        status_priority = {
            "offer": 0,
            "interview": 1,
            "applied": 2,
            "new": 3,
            "filtered": 4,
            "low_score": 5,
            "rejected": 6,
        }
        return min(
            rows,
            key=lambda r: (
                status_priority.get(r["status"], 99),
                -r["score"],
                r["date_scraped"],
            ),
        )["id"]

    # --- Helpers ---

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        comp = None
        if row["comp_min"] is not None:
            comp = Compensation(
                min_amount=row["comp_min"],
                max_amount=row["comp_max"],
                currency=row["comp_currency"] or "USD",
                interval=CompInterval(row["comp_interval"])
                if row["comp_interval"]
                else None,
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
            date_posted=date.fromisoformat(row["date_posted"])
            if row["date_posted"]
            else None,
            date_scraped=datetime.fromisoformat(row["date_scraped"]),
            score=row["score"],
            score_breakdown=json.loads(row["score_breakdown"]),
            status=row["status"],
            notes=row["notes"] or "",
            applied_date=date.fromisoformat(row["applied_date"])
            if row["applied_date"]
            else None,
            search_term=row["search_term"],
        )

    # --- ATS discovery subsystem (see ATS_DISCOVERY.md) ---

    # --- Company registry ---

    def upsert_company(self, company: Company) -> tuple[bool, int]:
        """Insert or update a company by name. Returns (is_new, row_id)."""
        cur = self.conn.execute(
            "SELECT id FROM companies WHERE name = ?", (company.name,)
        )
        existing = cur.fetchone()
        if existing:
            # Only overwrite the ATS provider when the incoming value is a real
            # provider, not the UNKNOWN default from a name-only candidate.
            if company.ats == ATSProvider.UNKNOWN:
                self.conn.execute(
                    """UPDATE companies SET
                        domain = COALESCE(?, domain),
                        careers_url = COALESCE(?, careers_url),
                        ats_slug = COALESCE(?, ats_slug),
                        last_verified_at = COALESCE(?, last_verified_at),
                        updated_at = datetime('now')
                    WHERE id = ?""",
                    (
                        company.domain,
                        company.careers_url,
                        company.ats_slug,
                        company.last_verified_at.isoformat()
                        if company.last_verified_at
                        else None,
                        existing["id"],
                    ),
                )
            else:
                self.conn.execute(
                    """UPDATE companies SET
                        domain = COALESCE(?, domain),
                        careers_url = COALESCE(?, careers_url),
                        ats = ?,
                        ats_slug = COALESCE(?, ats_slug),
                        last_verified_at = COALESCE(?, last_verified_at),
                        updated_at = datetime('now')
                    WHERE id = ?""",
                    (
                        company.domain,
                        company.careers_url,
                        company.ats.value,
                        company.ats_slug,
                        company.last_verified_at.isoformat()
                        if company.last_verified_at
                        else None,
                        existing["id"],
                    ),
                )
            # Union incoming provenance with existing so discovery history
            # accumulates across runs rather than being overwritten.
            existing_prov = json.loads(
                self.conn.execute(
                    "SELECT discovered_from FROM companies WHERE id = ?",
                    (existing["id"],),
                ).fetchone()["discovered_from"]
                or "[]"
            )
            merged_prov = existing_prov + [
                s for s in company.discovered_from if s not in existing_prov
            ]
            self.conn.execute(
                "UPDATE companies SET discovered_from = ? WHERE id = ?",
                (json.dumps(merged_prov), existing["id"]),
            )
            self.conn.commit()
            return False, existing["id"]

        cur = self.conn.execute(
            """INSERT INTO companies (
                name, domain, careers_url, ats, ats_slug, discovered_from,
                last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                company.name,
                company.domain,
                company.careers_url,
                company.ats.value,
                company.ats_slug,
                json.dumps(company.discovered_from),
                company.last_verified_at.isoformat()
                if company.last_verified_at
                else None,
            ),
        )
        self.conn.commit()
        return True, cur.lastrowid

    def get_company(self, company_id: int) -> Company | None:
        row = self.conn.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        return self._row_to_company(row) if row else None

    def get_company_by_name(self, name: str) -> Company | None:
        row = self.conn.execute(
            "SELECT * FROM companies WHERE name = ?", (name,)
        ).fetchone()
        return self._row_to_company(row) if row else None

    def get_companies(self, *, ats: str | None = None) -> list[Company]:
        if ats:
            rows = self.conn.execute(
                "SELECT * FROM companies WHERE ats = ?", (ats,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM companies").fetchall()
        return [self._row_to_company(r) for r in rows]

    def get_pollable_companies(self) -> list[Company]:
        """Companies with a resolved ATS slug, ready for polling."""
        rows = self.conn.execute(
            "SELECT * FROM companies WHERE ats != 'unknown' AND ats_slug IS NOT NULL"
        ).fetchall()
        return [self._row_to_company(r) for r in rows]

    @staticmethod
    def _row_to_company(row: sqlite3.Row) -> Company:
        return Company(
            id=row["id"],
            name=row["name"],
            domain=row["domain"],
            careers_url=row["careers_url"],
            ats=ATSProvider(row["ats"]),
            ats_slug=row["ats_slug"],
            discovered_from=json.loads(row["discovered_from"] or "[]"),
            last_verified_at=datetime.fromisoformat(row["last_verified_at"])
            if row["last_verified_at"]
            else None,
            created_at=datetime.fromisoformat(row["created_at"])
            if row["created_at"]
            else None,
        )

    # --- ATS jobs ---

    def upsert_ats_job(self, job: ATSJob) -> tuple[bool, int]:
        """Insert or update an ATS job by dedup_key. Returns (is_new, row_id).

        Source records are append-only: an existing row's status/last_seen
        are refreshed, but the source record itself is never destroyed.
        """
        cur = self.conn.execute(
            "SELECT id FROM ats_jobs WHERE dedup_key = ?", (job.dedup_key,)
        )
        existing = cur.fetchone()
        if existing:
            self.conn.execute(
                """UPDATE ats_jobs SET
                    last_seen_at = ?, updated_at = ?, status = ?,
                    repost = ?, title = ?, url = ?, apply_url = ?,
                    location_text = ?, city = ?, state = ?, country = ?,
                    is_remote = ?, hybrid = ?, employment_type = ?,
                    description_html = ?, description = ?,
                    salary_min = ?, salary_max = ?, currency = ?,
                    posted_at = ?
                WHERE id = ?""",
                (
                    job.last_seen_at.isoformat(),
                    job.updated_at.isoformat(),
                    job.status,
                    int(job.repost),
                    job.title,
                    job.url,
                    job.apply_url,
                    job.location_text,
                    job.location.city,
                    job.location.state,
                    job.location.country,
                    int(job.location.is_remote),
                    int(job.hybrid),
                    job.employment_type,
                    job.description_html,
                    job.description,
                    job.salary_min,
                    job.salary_max,
                    job.currency,
                    job.posted_at.isoformat() if job.posted_at else None,
                    existing["id"],
                ),
            )
            self.conn.commit()
            return False, existing["id"]

        cur = self.conn.execute(
            """INSERT INTO ats_jobs (
                dedup_key, source, source_id, company_id, company, title,
                url, apply_url, location_text, city, state, country,
                is_remote, hybrid, employment_type, description_html,
                description, salary_min, salary_max, currency, posted_at,
                first_seen_at, last_seen_at, updated_at, status, repost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.dedup_key,
                job.source.value,
                job.source_id,
                job.company_id,
                job.company,
                job.title,
                job.url,
                job.apply_url,
                job.location_text,
                job.location.city,
                job.location.state,
                job.location.country,
                int(job.location.is_remote),
                int(job.hybrid),
                job.employment_type,
                job.description_html,
                job.description,
                job.salary_min,
                job.salary_max,
                job.currency,
                job.posted_at.isoformat() if job.posted_at else None,
                job.first_seen_at.isoformat(),
                job.last_seen_at.isoformat(),
                job.updated_at.isoformat(),
                job.status,
                int(job.repost),
            ),
        )
        self.conn.commit()
        return True, cur.lastrowid

    def get_ats_job(self, job_id: int) -> ATSJob | None:
        row = self.conn.execute(
            "SELECT * FROM ats_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return self._row_to_ats_job(row) if row else None

    def get_ats_jobs(
        self,
        *,
        company_id: int | None = None,
        status: str | None = None,
        limit: int | None = 100,
    ) -> list[ATSJob]:
        clauses = []
        params: list = []
        if company_id is not None:
            clauses.append("company_id = ?")
            params.append(company_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM ats_jobs {where} ORDER BY first_seen_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_ats_job(r) for r in rows]

    def close_ats_jobs_missing_since(self, cutoff: datetime) -> int:
        """Mark open ATS jobs not seen since `cutoff` as closed.

        The caller decides the threshold (3 consecutive missing crawls).
        """
        cur = self.conn.execute(
            """UPDATE ats_jobs SET status = 'closed', closed_at = ?, updated_at = ?
            WHERE status = 'open' AND last_seen_at < ?""",
            (datetime.now().isoformat(), datetime.now().isoformat(), cutoff.isoformat()),
        )
        self.conn.commit()
        return cur.rowcount

    @staticmethod
    def _row_to_ats_job(row: sqlite3.Row) -> ATSJob:
        return ATSJob(
            id=row["id"],
            source=Site(row["source"]),
            source_id=row["source_id"],
            company_id=row["company_id"],
            company=row["company"],
            title=row["title"],
            url=row["url"],
            apply_url=row["apply_url"],
            location_text=row["location_text"],
            location=Location(
                city=row["city"],
                state=row["state"],
                country=row["country"],
                is_remote=bool(row["is_remote"]),
            ),
            hybrid=bool(row["hybrid"]),
            employment_type=row["employment_type"],
            description_html=row["description_html"],
            description=row["description"],
            salary_min=row["salary_min"],
            salary_max=row["salary_max"],
            currency=row["currency"],
            posted_at=datetime.fromisoformat(row["posted_at"])
            if row["posted_at"]
            else None,
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            closed_at=datetime.fromisoformat(row["closed_at"])
            if row["closed_at"]
            else None,
            status=row["status"],
            repost=bool(row["repost"]),
        )

    # --- Canonical jobs ---

    def upsert_canonical(self, job: CanonicalJob) -> tuple[bool, int]:
        """Insert or update a canonical record by canonical_key.

        Returns (is_new, row_id). Mutable fields are overwritten by the
        incoming projection (the caller merges before calling); first_seen_at
        is preserved on update.
        """
        cur = self.conn.execute(
            "SELECT id, first_seen_at FROM canonical_jobs WHERE canonical_key = ?",
            (job.canonical_key,),
        )
        existing = cur.fetchone()
        if existing:
            self.conn.execute(
                """UPDATE canonical_jobs SET
                    company_id = ?, company = ?, title = ?, location_text = ?,
                    city = ?, state = ?, country = ?, is_remote = ?, hybrid = ?,
                    employment_type = ?, salary_min = ?, salary_max = ?,
                    currency = ?, description_html = ?, description = ?,
                    url = ?, apply_url = ?, posted_at = ?,
                    last_seen_at = ?, updated_at = ?, status = ?, repost = ?,
                    source_ids = ?, score = ?, score_breakdown = ?
                WHERE id = ?""",
                (
                    job.company_id,
                    job.company,
                    job.title,
                    job.location_text,
                    job.location.city,
                    job.location.state,
                    job.location.country,
                    int(job.location.is_remote),
                    int(job.hybrid),
                    job.employment_type,
                    job.salary_min,
                    job.salary_max,
                    job.currency,
                    job.description_html,
                    job.description,
                    job.url,
                    job.apply_url,
                    job.posted_at.isoformat() if job.posted_at else None,
                    job.last_seen_at.isoformat(),
                    job.updated_at.isoformat(),
                    job.status,
                    int(job.repost),
                    json.dumps(job.source_ids),
                    job.score,
                    json.dumps(job.score_breakdown),
                    existing["id"],
                ),
            )
            self.conn.commit()
            return False, existing["id"]

        cur = self.conn.execute(
            """INSERT INTO canonical_jobs (
                canonical_key, company_id, company, title, location_text,
                city, state, country, is_remote, hybrid, employment_type,
                salary_min, salary_max, currency, description_html,
                description, url, apply_url, posted_at, first_seen_at,
                last_seen_at, updated_at, status, repost, source_ids,
                score, score_breakdown
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.canonical_key,
                job.company_id,
                job.company,
                job.title,
                job.location_text,
                job.location.city,
                job.location.state,
                job.location.country,
                int(job.location.is_remote),
                int(job.hybrid),
                job.employment_type,
                job.salary_min,
                job.salary_max,
                job.currency,
                job.description_html,
                job.description,
                job.url,
                job.apply_url,
                job.posted_at.isoformat() if job.posted_at else None,
                job.first_seen_at.isoformat(),
                job.last_seen_at.isoformat(),
                job.updated_at.isoformat(),
                job.status,
                int(job.repost),
                json.dumps(job.source_ids),
                job.score,
                json.dumps(job.score_breakdown),
            ),
        )
        self.conn.commit()
        return True, cur.lastrowid

    def get_canonical(self, canonical_key: str) -> CanonicalJob | None:
        row = self.conn.execute(
            "SELECT * FROM canonical_jobs WHERE canonical_key = ?",
            (canonical_key,),
        ).fetchone()
        return self._row_to_canonical(row) if row else None

    def get_canonical_jobs(
        self, *, status: str | None = None, company_id: int | None = None, limit: int | None = 100
    ) -> list[CanonicalJob]:
        clauses = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if company_id is not None:
            clauses.append("company_id = ?")
            params.append(company_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM canonical_jobs {where} ORDER BY last_seen_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_canonical(r) for r in rows]

    def close_canonical_jobs_missing_since(self, cutoff: datetime) -> int:
        """Mark open canonical jobs not seen since `cutoff` as closed."""
        cur = self.conn.execute(
            """UPDATE canonical_jobs SET status = 'closed', closed_at = ?, updated_at = ?
            WHERE status = 'open' AND last_seen_at < ?""",
            (datetime.now().isoformat(), datetime.now().isoformat(), cutoff.isoformat()),
        )
        self.conn.commit()
        return cur.rowcount

    @staticmethod
    def _row_to_canonical(row: sqlite3.Row) -> CanonicalJob:
        return CanonicalJob(
            id=row["id"],
            canonical_key=row["canonical_key"],
            company_id=row["company_id"],
            company=row["company"],
            title=row["title"],
            location_text=row["location_text"],
            location=Location(
                city=row["city"],
                state=row["state"],
                country=row["country"],
                is_remote=bool(row["is_remote"]),
            ),
            hybrid=bool(row["hybrid"]),
            employment_type=row["employment_type"],
            salary_min=row["salary_min"],
            salary_max=row["salary_max"],
            currency=row["currency"],
            description_html=row["description_html"],
            description=row["description"],
            url=row["url"],
            apply_url=row["apply_url"],
            posted_at=datetime.fromisoformat(row["posted_at"])
            if row["posted_at"]
            else None,
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            closed_at=datetime.fromisoformat(row["closed_at"])
            if row["closed_at"]
            else None,
            status=row["status"],
            repost=bool(row["repost"]),
            source_ids=json.loads(row["source_ids"] or "[]"),
            score=row["score"],
            score_breakdown=json.loads(row["score_breakdown"] or "{}"),
        )
