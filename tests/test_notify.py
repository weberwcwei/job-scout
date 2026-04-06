"""Tests for notification system: Telegram, email, macOS."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import httpx

from job_scout.config import (
    EmailConfig,
    MacOSNotifyConfig,
    NotificationsConfig,
    TelegramConfig,
)
from job_scout.models import Compensation, CompInterval, Job, Location, Site
from job_scout.notify import Notifier, _esc_md, send_telegram


def _make_job(score=60, company="TestCo", title="ML Engineer", **kwargs):
    return Job(
        source=Site.LINKEDIN,
        source_id="test-123",
        url="https://example.com/job/123",
        title=title,
        company=company,
        location=Location(city="San Jose", state="CA", is_remote=False),
        description="A great ML job",
        date_posted=date.today(),
        date_scraped=datetime.now(),
        score=score,
        score_breakdown={"keyword": 40, "company": 15, "title": 5, "recency": 0},
        compensation=Compensation(
            min_amount=180000, max_amount=250000, interval=CompInterval.YEARLY
        ),
        **kwargs,
    )


class TestSendTelegram:
    @patch("job_scout.notify.httpx.post")
    def test_sends_message(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42")

        result = send_telegram(text="hello", cfg=cfg)

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "123:ABC" in call_kwargs.args[0]
        assert call_kwargs.kwargs["json"]["chat_id"] == "42"
        assert call_kwargs.kwargs["json"]["text"] == "hello"
        assert call_kwargs.kwargs["json"]["parse_mode"] == "MarkdownV2"
        assert call_kwargs.kwargs["json"]["disable_web_page_preview"] is True

    @patch("job_scout.notify.httpx.post")
    def test_empty_token_skips(self, mock_post):
        cfg = TelegramConfig(enabled=True, bot_token="", chat_id="42")
        result = send_telegram(text="hello", cfg=cfg)
        assert result is False
        mock_post.assert_not_called()

    @patch("job_scout.notify.httpx.post")
    def test_empty_chat_id_skips(self, mock_post):
        cfg = TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="")
        result = send_telegram(text="hello", cfg=cfg)
        assert result is False
        mock_post.assert_not_called()

    @patch("job_scout.notify.httpx.post")
    def test_api_error_returns_false(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
        cfg = TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42")
        result = send_telegram(text="hello", cfg=cfg)
        assert result is False

    @patch("job_scout.notify.httpx.post")
    def test_network_error_returns_false(self, mock_post):
        mock_post.side_effect = httpx.ConnectError("timeout")
        cfg = TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42")
        result = send_telegram(text="hello", cfg=cfg)
        assert result is False


class TestNotifierTelegram:
    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_notify_telegram_sends_for_jobs(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42"),
        )
        notifier = Notifier(cfg)
        jobs = [_make_job(score=70), _make_job(score=60, company="OtherCo")]

        notifier.notify_new_jobs(jobs)

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        assert "42" == payload["chat_id"]
        assert "70" in payload["text"]
        assert "OtherCo" in payload["text"]

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_telegram_disabled_skips(self, mock_subprocess, mock_post):
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
        )
        notifier = Notifier(cfg)
        notifier.notify_new_jobs([_make_job()])
        mock_post.assert_not_called()

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_all_channels_fire(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=True),
            email=EmailConfig(
                enabled=True,
                username="a@b.com",
                app_password="pass",
                to_address="a@b.com",
            ),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42"),
        )
        notifier = Notifier(cfg)

        with patch("job_scout.notify.smtplib.SMTP") as mock_smtp:
            mock_smtp_instance = MagicMock()
            mock_smtp.return_value = mock_smtp_instance
            notifier.notify_new_jobs([_make_job()])

        # macOS notification
        mock_subprocess.assert_called_once()
        # Email
        mock_smtp.assert_called_once()
        # Telegram
        mock_post.assert_called_once()

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_message_format(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42"),
        )
        notifier = Notifier(cfg)
        notifier.notify_new_jobs([_make_job(score=75, company="NVIDIA", title="ML Eng")])

        text = mock_post.call_args.kwargs["json"]["text"]
        assert "75" in text
        assert "NVIDIA" in text
        assert "ML Eng" in text
        assert "example.com" in text


class TestNotifyFormatConcise:
    """Tests that notifications use concise salary and omit when missing."""

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_telegram_uses_concise_salary(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42"),
        )
        notifier = Notifier(cfg)
        # Job with salary — the _make_job helper has min=180000, max=250000
        notifier.notify_new_jobs([_make_job(score=70)])

        text = mock_post.call_args.kwargs["json"]["text"]
        # Should have concise salary like $180k-$250k, NOT $180,000
        assert "$180k" in text
        assert "$180,000" not in text

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_telegram_omits_salary_when_missing(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42"),
        )
        notifier = Notifier(cfg)
        job = _make_job(score=70)
        job.compensation = None
        notifier.notify_new_jobs([job])

        text = mock_post.call_args.kwargs["json"]["text"]
        assert "No salary" not in text

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_no_cap_on_jobs(self, mock_subprocess, mock_post):
        """All jobs sent, not just first 10."""
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42"),
        )
        notifier = Notifier(cfg)
        jobs = [_make_job(score=60 + i, company=f"Co{i}") for i in range(15)]
        notifier.notify_new_jobs(jobs)

        text = mock_post.call_args.kwargs["json"]["text"]
        # Job #15 (Co14) should be present — old code capped at 10
        assert "Co14" in text

    @patch("subprocess.run")
    def test_email_uses_concise_salary(self, mock_subprocess):
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(
                enabled=True,
                username="a@b.com",
                app_password="pass",
                to_address="a@b.com",
            ),
            telegram=TelegramConfig(enabled=False),
        )
        notifier = Notifier(cfg)
        with patch("job_scout.notify.smtplib.SMTP") as mock_smtp:
            mock_smtp_instance = MagicMock()
            mock_smtp.return_value = mock_smtp_instance
            notifier.notify_new_jobs([_make_job(score=70)])

        # Get the email body from send_message call
        call_args = mock_smtp_instance.send_message.call_args
        msg = call_args[0][0]
        body = msg.get_payload()[0].get_payload(decode=True).decode()
        assert "$180k" in body
        assert "$180,000" not in body

    @patch("subprocess.run")
    def test_email_omits_salary_when_missing(self, mock_subprocess):
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(
                enabled=True,
                username="a@b.com",
                app_password="pass",
                to_address="a@b.com",
            ),
            telegram=TelegramConfig(enabled=False),
        )
        notifier = Notifier(cfg)
        job = _make_job(score=70)
        job.compensation = None
        with patch("job_scout.notify.smtplib.SMTP") as mock_smtp:
            mock_smtp_instance = MagicMock()
            mock_smtp.return_value = mock_smtp_instance
            notifier.notify_new_jobs([job])

        call_args = mock_smtp_instance.send_message.call_args
        msg = call_args[0][0]
        body = msg.get_payload()[0].get_payload(decode=True).decode()
        assert "No salary" not in body


class TestEscMd:
    def test_escapes_special_chars(self):
        assert _esc_md("hello_world") == r"hello\_world"
        assert _esc_md("score: 100") == r"score: 100"
        assert _esc_md("test (foo)") == r"test \(foo\)"
        assert _esc_md("100.5") == r"100\.5"

    def test_empty_string(self):
        assert _esc_md("") == ""
