"""launchd plist management for macOS scheduling."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

from job_scout.config import LOG_DIR, ScheduleConfig

LABEL_PREFIX = "com.user.job-scout"
LEGACY_LABEL = "com.user.job-scout"  # old single-plist label
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
TASKS = ("scrape", "digest", "report")

# Keep PLIST_LABELS for backwards compat with tests referencing it
PLIST_LABELS = {k: f"{LABEL_PREFIX}.{k}" for k in TASKS}


def plist_labels(profile_name: str = "default") -> dict[str, str]:
    if profile_name == "default":
        return {k: f"{LABEL_PREFIX}.{k}" for k in TASKS}
    return {k: f"{LABEL_PREFIX}.{profile_name}.{k}" for k in TASKS}


def _get_python(project_dir: Path) -> str:
    venv_python = project_dir / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _generate_plist(
    label: str,
    command_args: list[str],
    schedule_key: str,
    schedule_value,
    log_prefix: str,
    log_dir: Path = LOG_DIR,
) -> dict:
    """Generate a launchd plist dict."""
    return {
        "Label": label,
        "ProgramArguments": command_args,
        schedule_key: schedule_value,
        "StandardOutPath": str(log_dir / f"{log_prefix}-stdout.log"),
        "StandardErrorPath": str(log_dir / f"{log_prefix}-stderr.log"),
        "RunAtLoad": schedule_key == "StartInterval",  # Only scrape runs at load
        "KeepAlive": False,
    }


def generate_plists(
    schedule: ScheduleConfig,
    project_dir: Path | None = None,
    profile_name: str = "default",
    config_path: Path | None = None,
) -> dict[str, dict]:
    """Generate all 3 plist dicts."""
    if project_dir is None:
        project_dir = Path.cwd()

    python_path = _get_python(project_dir)
    labels = plist_labels(profile_name)
    log_dir = LOG_DIR / profile_name if profile_name != "default" else LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    # Build base command args, insert --config for non-default profiles
    base_args = [python_path, "-m", "job_scout"]
    if config_path and profile_name != "default":
        base_args += ["--config", str(config_path.resolve())]

    return {
        labels["scrape"]: _generate_plist(
            label=labels["scrape"],
            command_args=base_args + ["scrape"],
            schedule_key="StartInterval",
            schedule_value=schedule.interval_hours * 3600,
            log_prefix="scrape",
            log_dir=log_dir,
        ),
        labels["digest"]: _generate_plist(
            label=labels["digest"],
            command_args=base_args + ["digest"],
            schedule_key="StartCalendarInterval",
            schedule_value={
                "Hour": schedule.digest_hour,
                "Minute": schedule.digest_minute,
            },
            log_prefix="digest",
            log_dir=log_dir,
        ),
        labels["report"]: _generate_plist(
            label=labels["report"],
            command_args=base_args + ["report"],
            schedule_key="StartCalendarInterval",
            schedule_value={
                "Hour": schedule.report_hour,
                "Minute": schedule.report_minute,
            },
            log_prefix="report",
            log_dir=log_dir,
        ),
    }


def install(
    schedule: ScheduleConfig,
    project_dir: Path | None = None,
    profile_name: str = "default",
    config_path: Path | None = None,
) -> list[Path]:
    """Install all 3 plists. Returns list of plist paths."""
    PLIST_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up legacy single plist if present
    legacy_path = PLIST_DIR / f"{LEGACY_LABEL}.plist"
    if legacy_path.exists():
        subprocess.run(["launchctl", "unload", str(legacy_path)], capture_output=True)
        legacy_path.unlink()

    plists = generate_plists(schedule, project_dir, profile_name, config_path)
    paths = []

    for label, plist_data in plists.items():
        path = PLIST_DIR / f"{label}.plist"
        # Unload if previously loaded
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        with open(path, "wb") as f:
            plistlib.dump(plist_data, f)
        subprocess.run(["launchctl", "load", str(path)], check=True)
        paths.append(path)

    return paths


def uninstall(profile_name: str = "default") -> None:
    """Remove plists for a profile, plus legacy single plist."""
    labels = plist_labels(profile_name)
    for label in labels.values():
        path = PLIST_DIR / f"{label}.plist"
        if path.exists():
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
            path.unlink()

    # Remove legacy plist
    legacy_path = PLIST_DIR / f"{LEGACY_LABEL}.plist"
    if legacy_path.exists():
        subprocess.run(["launchctl", "unload", str(legacy_path)], capture_output=True)
        legacy_path.unlink()


def status(profile_name: str = "default") -> dict[str, dict]:
    """Return per-plist status for a profile."""
    labels = plist_labels(profile_name)
    log_dir = LOG_DIR / profile_name if profile_name != "default" else LOG_DIR
    result = {}
    for name, label in labels.items():
        path = PLIST_DIR / f"{label}.plist"
        installed = path.exists()
        running = False
        if installed:
            check = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True,
                text=True,
            )
            running = check.returncode == 0
        result[name] = {
            "label": label,
            "installed": installed,
            "running": running,
            "plist_path": str(path),
        }
    result["log_dir"] = str(log_dir)
    return result
