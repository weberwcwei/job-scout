"""Tests for notification system: Telegram, email, macOS."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import httpx

from job_scout.config import (
    DiscordConfig,
    EmailConfig,
    MacOSNotifyConfig,
    NotificationsConfig,
    SlackConfig,
    TelegramConfig,
)
from job_scout.models import Compensation, CompInterval, Job, Location, Site
from job_scout.notify import (
    Notifier,
    _esc_discord,
    _esc_md,
    _esc_slack,
    send_discord,
    send_slack,
    send_telegram,
)


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
            slack=SlackConfig(
                enabled=True, webhook_url="https://hooks.slack.com/services/T/B/x"
            ),
            discord=DiscordConfig(
                enabled=True, webhook_url="https://discord.com/api/webhooks/123/abc"
            ),
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
        # Telegram + Slack + Discord = 3 httpx.post calls
        assert mock_post.call_count == 3

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
        notifier.notify_new_jobs(
            [_make_job(score=75, company="NVIDIA", title="ML Eng")]
        )

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


class TestNotifierProfileName:
    """Tests for profile name in notification messages."""

    @patch("subprocess.run")
    def test_macos_title_default(self, mock_subprocess):
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=True),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
        )
        notifier = Notifier(cfg)
        notifier.notify_new_jobs([_make_job()])

        script = mock_subprocess.call_args[0][0][-1]
        assert "job-scout:" in script
        assert "job-scout (" not in script

    @patch("subprocess.run")
    def test_macos_title_named(self, mock_subprocess):
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=True),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
        )
        notifier = Notifier(cfg, profile_name="frontend")
        notifier.notify_new_jobs([_make_job()])

        script = mock_subprocess.call_args[0][0][-1]
        assert "job-scout (frontend)" in script

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_telegram_prefix_default(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42"),
        )
        notifier = Notifier(cfg)
        notifier.notify_new_jobs([_make_job()])

        text = mock_post.call_args.kwargs["json"]["text"]
        assert "job\\-scout" in text
        assert "job\\-scout \\(" not in text

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_telegram_prefix_named(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="42"),
        )
        notifier = Notifier(cfg, profile_name="frontend")
        notifier.notify_new_jobs([_make_job()])

        text = mock_post.call_args.kwargs["json"]["text"]
        assert "frontend" in text

    @patch("subprocess.run")
    def test_email_subject_named(self, mock_subprocess):
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
        notifier = Notifier(cfg, profile_name="frontend")
        with patch("job_scout.notify.smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value = MagicMock()
            notifier.notify_new_jobs([_make_job()])

        call_args = mock_smtp.return_value.send_message.call_args
        msg = call_args[0][0]
        assert "frontend" in msg["Subject"]

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_slack_prefix_named(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
            slack=SlackConfig(
                enabled=True, webhook_url="https://hooks.slack.com/services/T/B/x"
            ),
            discord=DiscordConfig(enabled=False),
        )
        notifier = Notifier(cfg, profile_name="frontend")
        notifier.notify_new_jobs([_make_job()])

        text = mock_post.call_args.kwargs["json"]["text"]
        assert "frontend" in text

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_discord_prefix_named(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
            slack=SlackConfig(enabled=False),
            discord=DiscordConfig(
                enabled=True, webhook_url="https://discord.com/api/webhooks/123/abc"
            ),
        )
        notifier = Notifier(cfg, profile_name="frontend")
        notifier.notify_new_jobs([_make_job()])

        text = mock_post.call_args.kwargs["json"]["content"]
        assert "frontend" in text


class TestSendEmail:
    @patch("job_scout.notify.smtplib.SMTP")
    def test_sends_email_successfully(self, mock_smtp):
        from job_scout.notify import send_email

        mock_instance = MagicMock()
        mock_smtp.return_value = mock_instance

        cfg = EmailConfig(
            enabled=True,
            username="a@b.com",
            app_password="pass",
            to_address="recipient@b.com",
        )
        result = send_email(subject="Test", body="Hello", cfg=cfg)
        assert result is True
        mock_instance.starttls.assert_called_once()
        mock_instance.login.assert_called_once()
        mock_instance.send_message.assert_called_once()

    def test_missing_username_returns_false(self):
        from job_scout.notify import send_email

        cfg = EmailConfig(
            enabled=True, username="", app_password="pass", to_address="a@b.com"
        )
        result = send_email(subject="Test", body="Hello", cfg=cfg)
        assert result is False

    def test_missing_password_returns_false(self):
        from job_scout.notify import send_email

        cfg = EmailConfig(
            enabled=True, username="a@b.com", app_password="", to_address="a@b.com"
        )
        result = send_email(subject="Test", body="Hello", cfg=cfg)
        assert result is False

    def test_missing_to_address_returns_false(self):
        from job_scout.notify import send_email

        cfg = EmailConfig(
            enabled=True, username="a@b.com", app_password="pass", to_address=""
        )
        result = send_email(subject="Test", body="Hello", cfg=cfg)
        assert result is False

    @patch("job_scout.notify.smtplib.SMTP")
    def test_smtp_error_returns_false(self, mock_smtp):
        from job_scout.notify import send_email

        mock_smtp.side_effect = Exception("Connection refused")
        cfg = EmailConfig(
            enabled=True, username="a@b.com", app_password="pass", to_address="a@b.com"
        )
        result = send_email(subject="Test", body="Hello", cfg=cfg)
        assert result is False


class TestNotifierEmptyJobs:
    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_empty_jobs_sends_nothing(self, mock_subprocess, mock_post):
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
        notifier.notify_new_jobs([])

        mock_subprocess.assert_not_called()
        mock_post.assert_not_called()


class TestEscInternal:
    def test_esc_quotes(self):
        from job_scout.notify import _esc

        assert _esc('say "hello"') == 'say \\"hello\\"'

    def test_esc_backslash(self):
        from job_scout.notify import _esc

        assert _esc("path\\to\\file") == "path\\\\to\\\\file"

    def test_esc_empty(self):
        from job_scout.notify import _esc

        assert _esc("") == ""


class TestEscMd:
    def test_escapes_special_chars(self):
        assert _esc_md("hello_world") == r"hello\_world"
        assert _esc_md("score: 100") == r"score: 100"
        assert _esc_md("test (foo)") == r"test \(foo\)"
        assert _esc_md("100.5") == r"100\.5"

    def test_empty_string(self):
        assert _esc_md("") == ""


class TestEscSlack:
    def test_escapes_ampersand(self):
        assert _esc_slack("A & B") == "A &amp; B"

    def test_escapes_angle_brackets(self):
        assert _esc_slack("<tag>") == "&lt;tag&gt;"

    def test_no_escaping_needed(self):
        assert _esc_slack("hello world") == "hello world"

    def test_empty_string(self):
        assert _esc_slack("") == ""


class TestEscDiscord:
    def test_escapes_asterisk(self):
        assert _esc_discord("hello*world") == r"hello\*world"

    def test_escapes_underscore(self):
        assert _esc_discord("hello_world") == r"hello\_world"

    def test_escapes_tilde(self):
        assert _esc_discord("~strike~") == r"\~strike\~"

    def test_escapes_backtick(self):
        assert _esc_discord("`code`") == r"\`code\`"

    def test_escapes_pipe(self):
        assert _esc_discord("a|b") == r"a\|b"

    def test_escapes_backslash(self):
        assert _esc_discord(r"path\to") == r"path\\to"

    def test_empty_string(self):
        assert _esc_discord("") == ""


class TestSendSlack:
    @patch("job_scout.notify.httpx.post")
    def test_sends_message(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = SlackConfig(
            enabled=True, webhook_url="https://hooks.slack.com/services/T/B/x"
        )

        result = send_slack(text="hello", cfg=cfg)

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.args[0] == "https://hooks.slack.com/services/T/B/x"
        assert call_kwargs.kwargs["json"] == {"text": "hello"}

    @patch("job_scout.notify.httpx.post")
    def test_empty_url_returns_false(self, mock_post):
        cfg = SlackConfig(enabled=True, webhook_url="")
        result = send_slack(text="hello", cfg=cfg)
        assert result is False
        mock_post.assert_not_called()

    @patch("job_scout.notify.httpx.post")
    def test_api_error_returns_false(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
        cfg = SlackConfig(
            enabled=True, webhook_url="https://hooks.slack.com/services/T/B/x"
        )
        result = send_slack(text="hello", cfg=cfg)
        assert result is False

    @patch("job_scout.notify.httpx.post")
    def test_network_error_returns_false(self, mock_post):
        mock_post.side_effect = httpx.ConnectError("timeout")
        cfg = SlackConfig(
            enabled=True, webhook_url="https://hooks.slack.com/services/T/B/x"
        )
        result = send_slack(text="hello", cfg=cfg)
        assert result is False


class TestSendDiscord:
    @patch("job_scout.notify.httpx.post")
    def test_sends_message_204(self, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        cfg = DiscordConfig(
            enabled=True, webhook_url="https://discord.com/api/webhooks/123/abc"
        )

        result = send_discord(text="hello", cfg=cfg)

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.args[0] == "https://discord.com/api/webhooks/123/abc"
        assert call_kwargs.kwargs["json"] == {"content": "hello"}

    @patch("job_scout.notify.httpx.post")
    def test_empty_url_returns_false(self, mock_post):
        cfg = DiscordConfig(enabled=True, webhook_url="")
        result = send_discord(text="hello", cfg=cfg)
        assert result is False
        mock_post.assert_not_called()

    @patch("job_scout.notify.httpx.post")
    def test_api_error_returns_false(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
        cfg = DiscordConfig(
            enabled=True, webhook_url="https://discord.com/api/webhooks/123/abc"
        )
        result = send_discord(text="hello", cfg=cfg)
        assert result is False

    @patch("job_scout.notify.httpx.post")
    def test_network_error_returns_false(self, mock_post):
        mock_post.side_effect = httpx.ConnectError("timeout")
        cfg = DiscordConfig(
            enabled=True, webhook_url="https://discord.com/api/webhooks/123/abc"
        )
        result = send_discord(text="hello", cfg=cfg)
        assert result is False


class TestNotifierSlack:
    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_notify_slack_sends_for_jobs(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
            slack=SlackConfig(
                enabled=True, webhook_url="https://hooks.slack.com/services/T/B/x"
            ),
            discord=DiscordConfig(enabled=False),
        )
        notifier = Notifier(cfg)
        jobs = [_make_job(score=70), _make_job(score=60, company="OtherCo")]

        notifier.notify_new_jobs(jobs)

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        assert "text" in payload
        assert "70" in payload["text"]
        assert "OtherCo" in payload["text"]

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_slack_disabled_skips(self, mock_subprocess, mock_post):
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
            slack=SlackConfig(enabled=False),
            discord=DiscordConfig(enabled=False),
        )
        notifier = Notifier(cfg)
        notifier.notify_new_jobs([_make_job()])
        mock_post.assert_not_called()

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_slack_message_format(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
            slack=SlackConfig(
                enabled=True, webhook_url="https://hooks.slack.com/services/T/B/x"
            ),
            discord=DiscordConfig(enabled=False),
        )
        notifier = Notifier(cfg)
        notifier.notify_new_jobs(
            [_make_job(score=75, company="NVIDIA", title="ML Eng")]
        )

        text = mock_post.call_args.kwargs["json"]["text"]
        assert "75" in text
        assert "NVIDIA" in text
        assert "ML Eng" in text
        assert "example.com" in text


class TestNotifierDiscord:
    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_notify_discord_sends_for_jobs(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
            slack=SlackConfig(enabled=False),
            discord=DiscordConfig(
                enabled=True, webhook_url="https://discord.com/api/webhooks/123/abc"
            ),
        )
        notifier = Notifier(cfg)
        jobs = [_make_job(score=70), _make_job(score=60, company="OtherCo")]

        notifier.notify_new_jobs(jobs)

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        assert "content" in payload
        assert "70" in payload["content"]
        assert "OtherCo" in payload["content"]

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_discord_disabled_skips(self, mock_subprocess, mock_post):
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
            slack=SlackConfig(enabled=False),
            discord=DiscordConfig(enabled=False),
        )
        notifier = Notifier(cfg)
        notifier.notify_new_jobs([_make_job()])
        mock_post.assert_not_called()

    @patch("job_scout.notify.httpx.post")
    @patch("subprocess.run")
    def test_discord_message_format(self, mock_subprocess, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        cfg = NotificationsConfig(
            macos=MacOSNotifyConfig(enabled=False),
            email=EmailConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
            slack=SlackConfig(enabled=False),
            discord=DiscordConfig(
                enabled=True, webhook_url="https://discord.com/api/webhooks/123/abc"
            ),
        )
        notifier = Notifier(cfg)
        notifier.notify_new_jobs(
            [_make_job(score=75, company="NVIDIA", title="ML Eng")]
        )

        text = mock_post.call_args.kwargs["json"]["content"]
        assert "75" in text
        assert "NVIDIA" in text
        assert "ML Eng" in text
        assert "example.com" in text
