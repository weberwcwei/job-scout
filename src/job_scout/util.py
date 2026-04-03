"""Utility functions: HTML processing, salary parsing, job type extraction."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from job_scout.models import CompInterval, JobType


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def extract_emails(text: str | None) -> list[str]:
    if not text:
        return []
    return re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)


def extract_job_types(text: str | None) -> list[JobType]:
    if not text:
        return []
    patterns = {
        JobType.FULL_TIME: r"full\s?time",
        JobType.PART_TIME: r"part\s?time",
        JobType.INTERNSHIP: r"internship",
        JobType.CONTRACT: r"contract",
    }
    found = []
    for jt, pattern in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            found.append(jt)
    return found


def currency_parser(cur_str: str) -> float:
    cur_str = re.sub(r"[^-0-9.,]", "", cur_str)
    cur_str = re.sub(r"[.,]", "", cur_str[:-3]) + cur_str[-3:]
    if "." in cur_str[-3:]:
        return float(cur_str)
    elif "," in cur_str[-3:]:
        return float(cur_str.replace(",", "."))
    return float(cur_str)


def parse_compensation_interval(interval: str) -> CompInterval | None:
    mapping = {
        "DAY": CompInterval.DAILY,
        "YEAR": CompInterval.YEARLY,
        "HOUR": CompInterval.HOURLY,
        "WEEK": CompInterval.WEEKLY,
        "MONTH": CompInterval.MONTHLY,
    }
    return mapping.get(interval.upper())


def is_remote(title: str, description: str, location_str: str = "") -> bool:
    keywords = ["remote", "work from home", "wfh"]
    text = f"{title} {description} {location_str}".lower()
    return any(kw in text for kw in keywords)
