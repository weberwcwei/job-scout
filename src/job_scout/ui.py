"""Local web UI for browsing, filtering and updating job records.

Served by a stdlib-only HTTP server (no framework dependencies) so a base
``setup.sh`` install can run ``job-scout ui``. Reads the same SQLite DB the
CLI writes via :class:`job_scout.db.JobDB`.
"""

from __future__ import annotations

import json
import logging
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from job_scout.db import JobDB

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"

#: Statuses the UI offers as filter chips and status updates.
STATUSES = (
    "new",
    "applied",
    "interview",
    "offer",
    "rejected",
    "filtered",
    "expired",
)
EDITABLE_STATUSES = ("new", "applied", "interview", "offer", "rejected", "filtered")

#: Sort keys accepted by the API, mapped to SQL ORDER BY fragments.
SORT_OPTIONS = {
    "score": "score DESC, date_posted DESC",
    "date": "date_posted DESC, score DESC",
    "salary": "comp_max DESC, score DESC",
}


def _job_dict(job) -> dict[str, object]:
    """Project a Job onto a JSON-friendly dict for the UI."""
    comp = job.compensation
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "source": job.source.value,
        "url": job.url,
        "location": job.location.display,
        "is_remote": job.location.is_remote,
        "city": job.location.city,
        "state": job.location.state,
        "country": job.location.country,
        "description": job.description,
        "job_type": [t.value for t in job.job_type],
        "comp_min": comp.min_amount if comp else None,
        "comp_max": comp.max_amount if comp else None,
        "comp_currency": comp.currency if comp else None,
        "comp_interval": comp.interval.value if comp and comp.interval else None,
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "date_scraped": job.date_scraped.isoformat() if job.date_scraped else None,
        "score": job.score,
        "score_breakdown": job.score_breakdown,
        "status": job.status,
        "notes": job.notes,
        "applied_date": job.applied_date.isoformat() if job.applied_date else None,
        "search_term": job.search_term,
    }


class UIHandler(BaseHTTPRequestHandler):
    """Serves the single-page app and its JSON API against a JobDB."""

    server_version = "JobScoutUI/1.0"
    _scoped: JobDB | None = None

    def _send_json(self, payload: dict[str, object], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, name: str, content_type: str) -> None:
        path = WEB_DIR / name
        if not path.is_file():
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- handlers -----------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self._send_file("index.html", "text/html; charset=utf-8")
        elif parsed.path == "/app.css":
            self._send_file("app.css", "text/css; charset=utf-8")
        elif parsed.path == "/app.js":
            self._send_file("app.js", "text/javascript; charset=utf-8")
        elif parsed.path == "/api/meta":
            self._handle_meta()
        elif parsed.path == "/api/jobs":
            self._handle_jobs(parse_qs(parsed.query))
        elif parsed.path.startswith("/api/jobs/"):
            self._handle_job_detail(parsed.path)
        else:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/jobs/"):
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        job_id = self._parse_job_id(parsed.path)
        if job_id is None:
            self._send_json({"error": "bad job id"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON body"}, HTTPStatus.BAD_REQUEST)
            return
        if isinstance(payload, dict) is False:
            self._send_json({"error": "invalid JSON body"}, HTTPStatus.BAD_REQUEST)
            return
        self._handle_patch(job_id, payload)

    # -- API logic ----------------------------------------------------------

    @property
    def _db(self) -> JobDB:
        if self._scoped is None:
            raise RuntimeError("request database is unavailable")
        return self._scoped

    def _handle_meta(self) -> None:
        db = self._db
        rows = db.conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        sources = [
            r["source"]
            for r in db.conn.execute(
                "SELECT DISTINCT source FROM jobs ORDER BY source"
            ).fetchall()
        ]
        total = db.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        self._send_json(
            {
                "statuses": [{"status": s, "count": counts.get(s, 0)} for s in STATUSES],
                "sources": sources,
                "total": total,
            }
        )

    def _handle_jobs(self, query: dict[str, list[str]]) -> None:
        db = self._db
        status = self._first(query, "status") or None
        source = self._first(query, "source") or None
        q = (self._first(query, "q") or "").strip().lower()
        sort = SORT_OPTIONS.get(self._first(query, "sort") or "", SORT_OPTIONS["score"])
        limit = self._int_param(query, "limit")

        clauses = []
        params: list[object] = []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if q:
            clauses.append("(title LIKE ? OR company LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM jobs {where} ORDER BY {sort}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        rows = db.conn.execute(sql, params).fetchall()
        jobs = [_job_dict(db._row_to_job(r)) for r in rows]
        self._send_json({"jobs": jobs, "count": len(jobs)})

    def _handle_job_detail(self, path: str) -> None:
        db = self._db
        job_id = self._parse_job_id(path)
        if job_id is None:
            self._send_json({"error": "bad job id"}, HTTPStatus.BAD_REQUEST)
            return
        job = db.get_job(job_id)
        if job is None:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json({"job": _job_dict(job)})

    def _handle_patch(self, job_id: int, payload: dict[str, object]) -> None:
        db = self._db
        if "status" in payload and payload["status"] not in EDITABLE_STATUSES:
            self._send_json(
                {"error": f"status must be one of {EDITABLE_STATUSES}"},
                HTTPStatus.BAD_REQUEST,
            )
            return
        notes = payload.get("notes")
        if notes is not None and not isinstance(notes, str):
            self._send_json({"error": "notes must be a string"}, HTTPStatus.BAD_REQUEST)
            return
        job = db.get_job(job_id)
        if job is None:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        new_status = payload.get("status", job.status)
        if not isinstance(new_status, str):
            self._send_json({"error": "status must be a string"}, HTTPStatus.BAD_REQUEST)
            return
        db.update_status(job_id, new_status, notes=notes or "")
        updated = db.get_job(job_id)
        self._send_json({"ok": True, "job": _job_dict(updated)})

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _parse_job_id(path: str) -> int | None:
        try:
            return int(path.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _first(query: dict[str, list[str]], key: str) -> str | None:
        values = query.get(key)
        return values[0] if values else None

    @staticmethod
    def _int_param(query: dict[str, list[str]], key: str) -> int | None:
        raw = UIHandler._first(query, key)
        if raw is None:
            return None
        try:
            return max(1, int(raw))
        except ValueError:
            return None

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - base hook signature
        log.debug("%s - %s", self.address_string(), format % args)


def _create_server(db_path: Path, host: str, port: int) -> tuple[ThreadingHTTPServer, int]:
    """Bind a server for ``db_path``, trying successive ports from ``port``."""

    class Handler(UIHandler):
        def __init__(self, *args, **kwargs):
            self._scoped: JobDB | None = None
            super().__init__(*args, **kwargs)

        def setup(self) -> None:  # noqa: D401 - BaseHTTPRequestHandler hook
            super().setup()
            self._scoped = JobDB(db_path)

        def finish(self) -> None:
            if self._scoped is not None:
                self._scoped.close()
                self._scoped = None
            super().finish()

    for candidate in range(port, port + 50):
        try:
            server = ThreadingHTTPServer((host, candidate), Handler)
            return server, candidate
        except OSError:
            continue
    raise RuntimeError(f"no free port found in range {port}..{port + 49}")


def serve(db_path: str | Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    """Run the UI server until interrupted.

    Tries ``port`` and increments on OSError so a stale process never blocks
    startup. sqlite3 connections are not safe to share across threads, so a
    fresh :class:`JobDB` is opened per request and closed afterwards.
    """
    db_path = Path(db_path)
    server, bound_port = _create_server(db_path, host, port)

    url = f"http://{host}:{bound_port}/"
    log.info("job-scout UI on %s (profile DB: %s)", url, db_path)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
