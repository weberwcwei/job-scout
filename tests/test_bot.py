"""Tests for bot.py — Telegram bot routing, offset persistence, reply formatting."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from job_scout.bot import TelegramBot, _esc_md
from job_scout.db import JobDB
from job_scout.models import Job, Location, Site


def _make_job(
    source_id: str,
    *,
    job_id: int | None = None,
    company: str = "TestCo",
    title: str = "Engineer",
    score: int = 50,
    status: str = "new",
) -> Job:
    return Job(
        id=job_id,
        source=Site.LINKEDIN,
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=title,
        company=company,
        location=Location(city="SF", state="CA"),
        description="A test job posting with enough content to be meaningful.",
        score=score,
        score_breakdown={"keyword": score},
        status=status,
    )


class TestEscMd:
    def test_escapes_special_chars(self):
        assert _esc_md("Hello_world") == "Hello\\_world"
        assert _esc_md("#42") == "\\#42"
        assert _esc_md("a*b") == "a\\*b"

    def test_plain_text_unchanged(self):
        assert _esc_md("hello") == "hello"

    def test_multiple_specials(self):
        result = _esc_md("Job #42 (Google)")
        assert "\\#" in result
        assert "\\(" in result
        assert "\\)" in result


class TestOffsetPersistence:
    def test_load_offset_no_file(self, tmp_path):
        """Returns 0 when no offset file exists."""
        with patch("job_scout.bot.OFFSET_FILE", tmp_path / "nonexistent.json"):
            bot = _make_bot_with_mock_configs(tmp_path)
            assert bot._load_offset() == 0

    def test_persist_and_load_offset(self, tmp_path):
        offset_file = tmp_path / "offset.json"
        with (
            patch("job_scout.bot.OFFSET_FILE", offset_file),
            patch("job_scout.bot.OFFSET_DIR", tmp_path),
        ):
            bot = _make_bot_with_mock_configs(tmp_path)
            bot._persist_offset(12345)
            assert offset_file.exists()
            assert bot._load_offset() == 12345

    def test_load_corrupt_offset(self, tmp_path):
        """Returns 0 on corrupt offset file."""
        offset_file = tmp_path / "offset.json"
        offset_file.write_text("not json")
        with patch("job_scout.bot.OFFSET_FILE", offset_file):
            bot = _make_bot_with_mock_configs(tmp_path)
            assert bot._load_offset() == 0


class TestProfileRouting:
    def test_routes_to_correct_profile(self, tmp_path):
        """Messages route to the profile matching the chat_id."""
        bot = _make_bot_with_mock_configs(tmp_path)
        assert "12345" in bot._profiles
        assert bot._profiles["12345"].profile_name == "default"

    def test_ignores_unknown_chat_id(self, tmp_path):
        """Messages from unknown chat_ids are silently ignored."""
        bot = _make_bot_with_mock_configs(tmp_path)
        # Should not raise, just log and return
        with (
            patch("job_scout.bot.parse_status_update") as mock_parse,
            patch.object(bot, "_send_reply"),
        ):
            bot._process_message("token", "99999", "applied 42")
            mock_parse.assert_not_called()

    def test_multiple_profiles(self, tmp_path):
        """Multiple configs create separate profile entries."""
        bot = _make_bot_with_two_configs(tmp_path)
        assert len(bot._profiles) == 2
        assert "12345" in bot._profiles
        assert "67890" in bot._profiles


class TestProcessMessage:
    def test_applies_status_update(self, tmp_path):
        """Processes a status update and calls db.update_status."""
        bot = _make_bot_with_mock_configs(tmp_path)
        db = JobDB(bot._profiles["12345"].db_path)
        job = _make_job("a1", score=80, company="Google", title="Senior Eng")
        _, job_id = db.upsert_job(job)
        db.close()

        mock_result = {
            "updates": [{"job_id": job_id, "status": "applied", "notes": None}],
            "reply": None,
        }
        with (
            patch("job_scout.bot.parse_status_update", return_value=mock_result),
            patch.object(bot, "_send_reply") as mock_reply,
        ):
            bot._process_message("token", "12345", "applied to google")

        mock_reply.assert_called_once()
        reply_text = mock_reply.call_args[0][2]
        assert "Updated 1 job" in reply_text
        assert "Google" in reply_text

        # Verify DB was updated
        db = JobDB(bot._profiles["12345"].db_path)
        updated = db.get_job(job_id)
        assert updated.status == "applied"
        db.close()

    def test_handles_not_found_job(self, tmp_path):
        """Reports failure for non-existent job IDs."""
        bot = _make_bot_with_mock_configs(tmp_path)
        # Seed DB so we get past the empty check
        db = JobDB(bot._profiles["12345"].db_path)
        db.upsert_job(_make_job("seed1"))
        db.close()

        mock_result = {
            "updates": [{"job_id": 999, "status": "applied", "notes": None}],
            "reply": None,
        }
        with (
            patch("job_scout.bot.parse_status_update", return_value=mock_result),
            patch.object(bot, "_send_reply") as mock_reply,
        ):
            bot._process_message("token", "12345", "applied 999")

        reply_text = mock_reply.call_args[0][2]
        assert "999" in reply_text
        assert "not found" in reply_text

    def test_silent_for_non_status_messages(self, tmp_path):
        """Does not reply for messages that aren't status updates."""
        bot = _make_bot_with_mock_configs(tmp_path)
        # Ensure DB has at least one job so we get past the empty check
        db = JobDB(bot._profiles["12345"].db_path)
        db.upsert_job(_make_job("x1"))
        db.close()

        mock_result = {"updates": [], "reply": None}
        with (
            patch("job_scout.bot.parse_status_update", return_value=mock_result),
            patch.object(bot, "_send_reply") as mock_reply,
        ):
            bot._process_message("token", "12345", "hello there")

        mock_reply.assert_not_called()

    def test_forwards_clarification_reply(self, tmp_path):
        """Forwards LLM's clarification question to user."""
        bot = _make_bot_with_mock_configs(tmp_path)
        db = JobDB(bot._profiles["12345"].db_path)
        db.upsert_job(_make_job("x1"))
        db.close()

        mock_result = {
            "updates": [],
            "reply": "Which job do you mean?",
        }
        with (
            patch("job_scout.bot.parse_status_update", return_value=mock_result),
            patch.object(bot, "_send_reply") as mock_reply,
        ):
            bot._process_message("token", "12345", "applied to that one")

        reply_text = mock_reply.call_args[0][2]
        assert "Which job" in reply_text

    def test_empty_db_sends_no_jobs_message(self, tmp_path):
        """When DB is empty, sends helpful message instead of calling LLM."""
        bot = _make_bot_with_mock_configs(tmp_path)

        with (
            patch("job_scout.bot.parse_status_update") as mock_parse,
            patch.object(bot, "_send_reply") as mock_reply,
        ):
            bot._process_message("token", "12345", "applied 42")

        mock_parse.assert_not_called()
        mock_reply.assert_called_once()
        assert "No recent jobs" in mock_reply.call_args[0][2]


class TestDBStatusUpdates:
    """Test the new status values work with the DB layer."""

    def test_interview_sets_applied_date(self, tmp_path):
        db = JobDB(tmp_path / "test.db")
        job = _make_job("i1")
        _, job_id = db.upsert_job(job)
        db.update_status(job_id, "interview")
        updated = db.get_job(job_id)
        assert updated.status == "interview"
        assert updated.applied_date is not None
        db.close()

    def test_offer_sets_applied_date(self, tmp_path):
        db = JobDB(tmp_path / "test.db")
        job = _make_job("o1")
        _, job_id = db.upsert_job(job)
        db.update_status(job_id, "offer")
        updated = db.get_job(job_id)
        assert updated.status == "offer"
        assert updated.applied_date is not None
        db.close()

    def test_interview_preserves_existing_applied_date(self, tmp_path):
        db = JobDB(tmp_path / "test.db")
        job = _make_job("p1")
        _, job_id = db.upsert_job(job)
        # First mark as applied (sets applied_date)
        db.update_status(job_id, "applied")
        first_date = db.get_job(job_id).applied_date
        # Then mark as interview (should preserve applied_date)
        db.update_status(job_id, "interview")
        updated = db.get_job(job_id)
        assert updated.status == "interview"
        assert updated.applied_date == first_date
        db.close()

    def test_get_recent_jobs(self, tmp_path):
        db = JobDB(tmp_path / "test.db")
        # Insert a few jobs
        db.upsert_job(_make_job("r1", score=80))
        db.upsert_job(_make_job("r2", score=70))
        _, id3 = db.upsert_job(_make_job("r3", score=60))
        db.update_status(id3, "applied")

        jobs = db.get_recent_jobs(days=14)
        assert len(jobs) == 3
        # Ordered by score DESC
        assert jobs[0].score >= jobs[1].score
        db.close()

    def test_get_recent_jobs_respects_limit(self, tmp_path):
        db = JobDB(tmp_path / "test.db")
        for i in range(10):
            db.upsert_job(_make_job(f"lim-{i}", score=50 + i))
        jobs = db.get_recent_jobs(days=14, limit=5)
        assert len(jobs) == 5
        db.close()


# --- Helpers ---


def _make_config_yaml(
    tmp_path: Path,
    filename: str = "config.yaml",
    chat_id: str = "12345",
    bot_token: str = "test-bot-token",
    api_key: str = "test-gemini-key",
) -> Path:
    """Create a minimal config YAML for testing."""
    # db_path must point into tmp_path to avoid touching real DB
    db_file = tmp_path / f"test-{filename.replace('.yaml', '')}.db"
    config = {
        "profile": {
            "name": "Test User",
            "target_title": "Engineer",
            "keywords": {"critical": ["python"]},
        },
        "search": {
            "terms": ["engineer"],
            "locations": ["San Francisco CA"],
        },
        "notifications": {
            "telegram": {
                "enabled": True,
                "bot_token": bot_token,
                "chat_id": chat_id,
            },
        },
        "bot": {
            "gemini_api_key": api_key,
        },
        "db_path": str(db_file),
    }
    import yaml

    path = tmp_path / filename
    path.write_text(yaml.dump(config))
    return path


def _make_bot_with_mock_configs(tmp_path: Path) -> TelegramBot:
    """Create a TelegramBot with a single test config."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _make_config_yaml(config_dir)

    with (
        patch("job_scout.bot.OFFSET_DIR", tmp_path / "bot"),
        patch("job_scout.bot.OFFSET_FILE", tmp_path / "bot" / "offset.json"),
    ):
        return TelegramBot(config_dir=config_dir)


def _make_bot_with_two_configs(tmp_path: Path) -> TelegramBot:
    """Create a TelegramBot with two test configs."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _make_config_yaml(config_dir, "config.yaml", chat_id="12345")
    _make_config_yaml(config_dir, "lucy.yaml", chat_id="67890")

    with (
        patch("job_scout.bot.OFFSET_DIR", tmp_path / "bot"),
        patch("job_scout.bot.OFFSET_FILE", tmp_path / "bot" / "offset.json"),
    ):
        return TelegramBot(config_dir=config_dir)
