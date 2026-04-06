"""Notification system: macOS native + email + Telegram."""

from __future__ import annotations

import logging
import smtplib
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from job_scout.config import NotificationsConfig
from job_scout.models import Job

log = logging.getLogger("job_scout.notify")


class Notifier:
    def __init__(self, config: NotificationsConfig):
        self.config = config

    def notify_new_jobs(self, jobs: list[Job]) -> None:
        if not jobs:
            return
        if self.config.macos.enabled:
            self._notify_macos(jobs)
        if self.config.email.enabled:
            self._notify_email(jobs)
        if self.config.telegram.enabled:
            self._notify_telegram(jobs)

    def _notify_macos(self, jobs: list[Job]) -> None:
        if len(jobs) == 1:
            job = jobs[0]
            title = f"job-scout: {job.company}"
            body = f"{job.title} (Score: {job.score})"
        else:
            title = f"job-scout: {len(jobs)} new matches"
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

        lines = [f"job-scout alert — {len(jobs)} new match(es)\n"]
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
            subject=f"job-scout: {len(jobs)} new match(es)",
            body=body,
            cfg=cfg,
        )

    def _notify_telegram(self, jobs: list[Job]) -> None:
        cfg = self.config.telegram
        if not cfg.bot_token or not cfg.chat_id:
            log.warning("Telegram bot_token or chat_id not configured")
            return

        lines = [f"*job\\-scout* — {len(jobs)} new match\\(es\\)\n"]
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


def send_email(subject: str, body: str, cfg) -> bool:
    """Send a plain-text email via Gmail SMTP."""
    if not cfg.username or not cfg.app_password or not cfg.to_address:
        log.error(
            "Email not configured (missing username, app_password, or to_address)"
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.username
    msg["To"] = cfg.to_address
    msg.attach(MIMEText(body, "plain"))

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
