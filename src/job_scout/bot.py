"""Telegram bot: long-polling listener for job status updates via natural language."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from job_scout.config import (
    AppConfig,
    BotConfig,
    XDG_CONFIG_DIR,
    load_config,
    resolve_data_paths,
)
from job_scout.db import JobDB
from job_scout.llm import parse_status_update

log = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".local" / "share" / "job-scout"
OFFSET_DIR = DATA_DIR / "bot"
OFFSET_FILE = OFFSET_DIR / "update-offset.json"


def _esc_md(s: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    for ch in r"_*[]()~`>#+-=|{}.!":
        s = s.replace(ch, f"\\{ch}")
    return s


@dataclass
class ProfileContext:
    config: AppConfig
    db_path: Path
    profile_name: str
    bot_token: str


class TelegramBot:
    """Long-polling Telegram bot that routes status updates to per-profile DBs."""

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or XDG_CONFIG_DIR
        self._profiles: dict[str, ProfileContext] = {}  # chat_id -> context
        self._bot_tokens: set[str] = set()
        self._api_key: str = ""
        self._bot_config: BotConfig = BotConfig()
        self._scan_configs()

    def _scan_configs(self) -> None:
        """Scan config dir for all profiles and build routing map."""
        config_files = sorted(self.config_dir.glob("*.yaml"))
        if not config_files:
            raise SystemExit(f"No config files found in {self.config_dir}")

        for path in config_files:
            try:
                cfg = load_config(path)
            except SystemExit:
                log.warning(f"Skipping invalid config: {path}")
                continue

            tg = cfg.notifications.telegram
            if not tg.enabled or not tg.bot_token or not tg.chat_id:
                continue

            paths = resolve_data_paths(path, cfg)
            self._profiles[tg.chat_id] = ProfileContext(
                config=cfg,
                db_path=paths.db,
                profile_name=paths.profile_name,
                bot_token=tg.bot_token,
            )
            self._bot_tokens.add(tg.bot_token)

            # Resolve API key: env var first (handled by resolve_api_key), then first config that has it
            if not self._api_key:
                key = cfg.bot.resolve_api_key()
                if key:
                    self._api_key = key
                    self._bot_config = cfg.bot

        if not self._profiles:
            raise SystemExit(
                "No profiles with Telegram enabled found. "
                "Configure notifications.telegram in your config YAML."
            )
        if not self._api_key:
            raise SystemExit(
                "No Gemini API key found. Set GEMINI_API_KEY env var "
                "or bot.gemini_api_key in config."
            )

        log.info(
            f"Bot ready: {len(self._profiles)} profile(s), "
            f"{len(self._bot_tokens)} bot token(s)"
        )

    def run(self) -> None:
        """Main blocking polling loop."""
        offset = self._load_offset()
        timeout = self._bot_config.poll_timeout
        backoff = 1

        # Use the first bot token (typical: one bot serves all profiles)
        bot_token = next(iter(self._bot_tokens))
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"

        log.info("Starting Telegram long-polling...")
        while True:
            try:
                resp = httpx.get(
                    url,
                    params={
                        "offset": offset,
                        "timeout": timeout,
                        "allowed_updates": '["message"]',
                    },
                    timeout=timeout + 10,
                )
                if resp.status_code != 200:
                    log.error(f"getUpdates returned {resp.status_code}: {resp.text}")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    log.error(f"getUpdates not ok: {data}")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                    continue

                backoff = 1  # reset on success
                updates = data.get("result", [])

                for update in updates:
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")

                    if chat_id and text:
                        self._process_message(bot_token, chat_id, text)

                if updates:
                    self._persist_offset(offset)

            except httpx.TimeoutException:
                continue  # normal for long-polling
            except KeyboardInterrupt:
                log.info("Bot stopped by user")
                break
            except Exception as e:
                log.error(f"Polling error: {e}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _process_message(self, bot_token: str, chat_id: str, text: str) -> None:
        """Route an incoming message to the correct profile and process it."""
        ctx = self._profiles.get(chat_id)
        if not ctx:
            log.debug(f"Ignoring message from unknown chat_id: {chat_id}")
            return

        log.info(f"Processing message from {ctx.profile_name}: {text[:80]}")

        db = JobDB(ctx.db_path)
        try:
            jobs = db.get_recent_jobs(
                days=self._bot_config.job_context_days,
            )

            if not jobs:
                self._send_reply(
                    bot_token,
                    chat_id,
                    "No recent jobs in your database yet\\.",
                )
                return

            result = parse_status_update(
                message=text,
                jobs=jobs,
                api_key=self._api_key,
                model=self._bot_config.gemini_model,
            )

            updates = result.get("updates", [])
            reply = result.get("reply")

            if not updates and not reply:
                # Not a status update, silently ignore
                return

            if not updates and reply:
                # LLM wants to ask for clarification
                self._send_reply(bot_token, chat_id, _esc_md(reply))
                return

            # Apply updates and build confirmation
            successes = []
            failures = []
            for upd in updates:
                job_id = upd.get("job_id")
                status = upd.get("status", "")
                notes = upd.get("notes") or ""

                job = db.get_job(job_id)
                if not job:
                    failures.append(job_id)
                    continue

                db.update_status(job_id, status, notes)
                successes.append(
                    f"  \\#{job.id} {_esc_md(job.company)}: {_esc_md(job.title)} \\→ {_esc_md(status)}"
                )

            reply_lines = []
            if successes:
                reply_lines.append(f"\\u2705 Updated {len(successes)} job\\(s\\):")
                reply_lines.extend(successes)
            if failures:
                for fid in failures:
                    reply_lines.append(f"\\u26a0\\ufe0f Job \\#{fid} not found")

            if reply_lines:
                self._send_reply(bot_token, chat_id, "\n".join(reply_lines))

        finally:
            db.close()

    def _send_reply(self, bot_token: str, chat_id: str, text: str) -> None:
        """Send a MarkdownV2 reply to a Telegram chat."""
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            resp = httpx.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                log.error(f"sendMessage failed: {resp.status_code}: {resp.text}")
        except Exception as e:
            log.error(f"Failed to send reply: {e}")

    def _load_offset(self) -> int:
        """Load the last update offset from disk."""
        if OFFSET_FILE.exists():
            try:
                data = json.loads(OFFSET_FILE.read_text())
                return data.get("last_update_id", 0)
            except (json.JSONDecodeError, KeyError):
                log.warning("Corrupt offset file, starting from 0")
        return 0

    def _persist_offset(self, offset: int) -> None:
        """Persist the update offset to disk."""
        OFFSET_DIR.mkdir(parents=True, exist_ok=True)
        OFFSET_FILE.write_text(json.dumps({"last_update_id": offset}))
