"""Notification system: macOS native + email + Telegram + Slack + Discord."""

from __future__ import annotations

import logging
import smtplib
import subprocess
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

import httpx

from job_scout.config import DiscordConfig, NotificationsConfig, SlackConfig
from job_scout.models import Job

log = logging.getLogger("job_scout.notify")


class Notifier:
    def __init__(self, config: NotificationsConfig, profile_name: str = "default"):
        self.config = config
        self.profile_name = profile_name

    def notify_new_jobs(self, jobs: list[Job]) -> None:
        if not jobs:
            return
        if self.config.macos.enabled:
            self._notify_macos(jobs)
        if self.config.email.enabled:
            self._notify_email(jobs)
        if self.config.telegram.enabled:
            self._notify_telegram(jobs)
        if self.config.slack.enabled:
            self._notify_slack(jobs)
        if self.config.discord.enabled:
            self._notify_discord(jobs)

    def _notify_macos(self, jobs: list[Job]) -> None:
        prefix = (
            f"job-scout ({self.profile_name})"
            if self.profile_name != "default"
            else "job-scout"
        )
        if len(jobs) == 1:
            job = jobs[0]
            title = f"{prefix}: {job.company}"
            body = f"{job.title} (Score: {job.score})"
        else:
            title = f"{prefix}: {len(jobs)} new matches"
            top3 = jobs[:3]
            body = "\\n".join(f"{j.company}: {j.title} ({j.score})" for j in top3)
            if len(jobs) > 3:
                body += f"\\n... and {len(jobs) - 3} more"

        script = (
            f'display notification "{_esc(body)}" '
            f'with title "{_esc(title)}" '
            f'sound name "{self.config.macos.sound}"'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                timeout=5,
            )
        except Exception as e:
            log.error(f"macOS notification failed: {e}")

    def _notify_email(self, jobs: list[Job]) -> None:
        cfg = self.config.email
        if not cfg.username or not cfg.app_password or not cfg.to_address:
            log.warning("Email not configured — skipping alert")
            return

        prefix = (
            f"job-scout ({self.profile_name})"
            if self.profile_name != "default"
            else "job-scout"
        )
        lines = [f"{prefix} alert — {len(jobs)} new match(es)\n"]
        for job in jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"#{job.id} " if job.id else ""
            loc_line = f"  {job.location.display}"
            if salary:
                loc_line += f" | {salary}"
            lines.append(
                f"[{job.score}] (kw:{kw}) {id_tag}{job.company}: {job.title}\n"
                f"{loc_line}\n"
                f"  {job.url}\n"
            )
        body = "\n".join(lines)

        send_email(
            subject=f"{prefix}: {len(jobs)} new match(es)",
            body=body,
            cfg=cfg,
        )

    def _notify_telegram(self, jobs: list[Job]) -> None:
        cfg = self.config.telegram
        if not cfg.bot_token or not cfg.chat_id:
            log.warning("Telegram bot_token or chat_id not configured")
            return

        if self.profile_name != "default":
            header = f"*job\\-scout \\({_esc_md(self.profile_name)}\\)* — {len(jobs)} new match\\(es\\)\n"
        else:
            header = f"*job\\-scout* — {len(jobs)} new match\\(es\\)\n"
        lines = [header]
        for job in jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"\\#{job.id} " if job.id else ""
            loc_line = f"  {_esc_md(job.location.display)}"
            if salary:
                loc_line += f" \\| {_esc_md(salary)}"
            lines.append(
                f"*{job.score}* \\(kw:{kw}\\) \\| {id_tag}[{_esc_md(job.company)}: {_esc_md(job.title)}]({job.url})\n"
                f"{loc_line}"
            )
        text = "\n".join(lines)

        send_telegram(text=text, cfg=cfg)

    def _notify_slack(self, jobs: list[Job]) -> None:
        cfg = self.config.slack
        if not cfg.webhook_url:
            log.warning("Slack webhook_url not configured")
            return

        prefix = (
            f"*job-scout ({self.profile_name})* "
            if self.profile_name != "default"
            else "*job-scout* "
        )
        lines = [f"{prefix}— {len(jobs)} new match(es)\n"]
        for job in jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"#{job.id} " if job.id else ""
            loc_line = f"  {_esc_slack(job.location.display)}"
            if salary:
                loc_line += f" | {_esc_slack(salary)}"
            lines.append(
                f"*{_esc_slack(job.company)}: {_esc_slack(job.title)}*\n"
                f"Score: {job.score} | keywords: {kw} | {id_tag}{loc_line}\n"
                f"{job.url}"
            )
        text = "\n".join(lines)

        send_slack(text=text, cfg=cfg)

    def _notify_discord(self, jobs: list[Job]) -> None:
        cfg = self.config.discord
        if not cfg.webhook_url:
            log.warning("Discord webhook_url not configured")
            return

        prefix = (
            f"**job-scout ({self.profile_name})**"
            if self.profile_name != "default"
            else "**job-scout**"
        )
        lines = [f"{prefix} — {len(jobs)} new match(es)\n"]
        for job in jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"#{job.id} " if job.id else ""
            loc_line = f"  {_esc_discord(job.location.display)}"
            if salary:
                loc_line += f" | {_esc_discord(salary)}"
            lines.append(
                f"**{_esc_discord(job.company)}: {_esc_discord(job.title)}**\n"
                f"Score: {job.score} | keywords: {kw} | {id_tag}{loc_line}\n"
                f"{job.url}"
            )
        text = "\n".join(lines)

        send_discord(text=text, cfg=cfg)


def send_telegram(text: str, cfg) -> bool:
    """Send a message via Telegram Bot API."""
    if not cfg.bot_token or not cfg.chat_id:
        log.error("Telegram not configured (missing bot_token or chat_id)")
        return False

    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": cfg.chat_id,
                "text": text,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("Telegram message sent")
            return True
        log.error(f"Telegram API returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Telegram notification failed: {e}")
        return False


def send_email(
    subject: str, body: str, cfg, attachment: Path | None = None
) -> bool:
    """Send a plain-text email via Gmail SMTP, optionally with a file attachment."""
    if not cfg.username or not cfg.app_password or not cfg.to_address:
        log.error(
            "Email not configured (missing username, app_password, or to_address)"
        )
        return False

    msg = MIMEMultipart("mixed" if attachment else "alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.username
    msg["To"] = cfg.to_address
    msg.attach(MIMEText(body, "plain"))

    if attachment and attachment.exists():
        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment.read_bytes())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition", f"attachment; filename={attachment.name}"
        )
        msg.attach(part)

    smtp = None
    try:
        smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port)
        smtp.starttls()
        smtp.login(cfg.username, cfg.app_password)
        smtp.send_message(msg)
        log.info(f"Email sent: {subject}")
        return True
    except Exception as e:
        log.error(f"Email failed: {e}")
        return False
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                pass


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _esc_md(s: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    for ch in r"_*[]()~`>#+-=|{}.!":
        s = s.replace(ch, f"\\{ch}")
    return s


def _esc_slack(s: str) -> str:
    """Escape special characters for Slack mrkdwn."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_discord(s: str) -> str:
    """Escape special characters for Discord markdown."""
    for ch in r"\*_~`|":
        s = s.replace(ch, f"\\{ch}")
    return s


def send_slack(text: str, cfg: SlackConfig) -> bool:
    """POST to Slack incoming webhook. Returns True on success."""
    if not cfg.webhook_url:
        log.error("Slack not configured (missing webhook_url)")
        return False

    try:
        resp = httpx.post(cfg.webhook_url, json={"text": text}, timeout=10)
        if 200 <= resp.status_code < 300:
            log.info("Slack message sent")
            return True
        log.error(f"Slack webhook returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Slack notification failed: {e}")
        return False


def send_discord(text: str, cfg: DiscordConfig) -> bool:
    """POST to Discord webhook. Returns True on success."""
    if not cfg.webhook_url:
        log.error("Discord not configured (missing webhook_url)")
        return False

    try:
        resp = httpx.post(cfg.webhook_url, json={"content": text}, timeout=10)
        if 200 <= resp.status_code < 300:
            log.info("Discord message sent")
            return True
        log.error(f"Discord webhook returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Discord notification failed: {e}")
        return False
