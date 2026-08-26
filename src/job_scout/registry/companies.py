"""Company resolution: name -> domain -> careers page -> ATS -> board slug.

The resolver fills in a :class:`~job_scout.models.Company`'s missing fields:
canonical domain, careers URL, ATS provider, and board slug. It never assumes
the company name equals the ATS identifier — the slug is only extracted from a
board URL actually seen on the careers page.

All network access is best-effort: failures leave fields untouched and log,
never raise.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from job_scout.config import ScrapingConfig
from job_scout.models import ATSProvider, Company

log = logging.getLogger("job_scout.registry")

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

#: Board URL patterns -> provider, in priority order.
_BOARD_PATTERNS: list[tuple[ATSProvider, re.Pattern]] = [
    (ATSProvider.GREENHOUSE, re.compile(r"boards\.greenhouse\.io/([A-Za-z0-9_-]+)")),
    (ATSProvider.LEVER, re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)")),
    (ATSProvider.ASHBY, re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)")),
]

#: Generic ATS signatures in page text, when no board URL is found.
_ATS_SIGNATURES: list[tuple[ATSProvider, str]] = [
    (ATSProvider.GREENHOUSE, "greenhouse.io"),
    (ATSProvider.LEVER, "lever.co"),
    (ATSProvider.ASHBY, "ashbyhq.com"),
]

#: Candidate careers paths tried before falling back to homepage link scan.
_CAREERS_PATHS = ("/careers", "/jobs", "/careers/", "/about/careers", "/company/careers")

#: Anchor-text hints that a link leads to a careers page.
_CAREERS_HINTS = ("career", "job", "work with us", "openings", "vacancies", "join us")


class CompanyResolver:
    """Resolve a company's careers infrastructure from its name/domain."""

    def __init__(self, config: ScrapingConfig):
        self.config = config

    def resolve(self, company: Company) -> Company:
        """Return a new Company with as many fields resolved as possible."""
        resolved = company.model_copy(deep=True)

        domain = resolved.domain or self._resolve_domain(resolved.name)
        if domain:
            resolved.domain = domain

        if not resolved.careers_url and domain:
            resolved.careers_url = self._find_careers_url(domain)

        if resolved.ats == ATSProvider.UNKNOWN or not resolved.ats_slug:
            self._detect_ats(resolved)

        if resolved.ats != ATSProvider.UNKNOWN or resolved.ats_slug:
            resolved.last_verified_at = _now()
        return resolved

    # -- domain -------------------------------------------------------------

    def _resolve_domain(self, name: str) -> str | None:
        """Guess a canonical domain from a company name and verify it responds."""
        slug = _slugify(name)
        if not slug:
            return None
        candidates = [f"{slug}.com.au", f"{slug}.com", f"{slug}.io", f"{slug}.co"]
        with self._client() as client:
            for domain in candidates:
                try:
                    resp = client.get(f"https://{domain}", follow_redirects=True)
                except httpx.HTTPError:
                    continue
                if resp.status_code == 200:
                    return domain
        return None

    # -- careers page -------------------------------------------------------

    def _find_careers_url(self, domain: str) -> str | None:
        with self._client() as client:
            # Fast path: common paths.
            for path in _CAREERS_PATHS:
                url = f"https://{domain}{path}"
                if self._is_live(client, url):
                    return url
            # Fallback: scan the homepage for a careers link.
            homepage = f"https://{domain}"
            try:
                resp = client.get(homepage)
            except httpx.HTTPError:
                return None
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.text, "html.parser")
            for anchor in soup.select("a[href]"):
                href = anchor.get("href")
                href = href[0] if isinstance(href, list) else (href or "")
                text = (anchor.get_text(" ", strip=True) or "").lower()
                if any(hint in text for hint in _CAREERS_HINTS) or any(
                    hint in href.lower() for hint in _CAREERS_HINTS
                ):
                    full = _absolutize(homepage, href)
                    if full and self._is_live(client, full):
                        return full
        return None

    # -- ATS detection ------------------------------------------------------

    def _detect_ats(self, company: Company) -> None:
        """Set company.ats / ats_slug from the careers page, if detectable."""
        url = company.careers_url or (
            f"https://{company.domain}" if company.domain else None
        )
        if not url:
            return
        with self._client() as client:
            try:
                resp = client.get(url)
            except httpx.HTTPError:
                return
            if resp.status_code != 200:
                return
            html = resp.text
            # Board URL present on the page: strongest signal.
            for provider, pattern in _BOARD_PATTERNS:
                m = pattern.search(html)
                if m and m.group(1).lower() not in ("jobs", "postings", "job-board"):
                    company.ats = provider
                    company.ats_slug = m.group(1)
                    return
            # Otherwise, generic signature in text.
            text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
            for provider, signature in _ATS_SIGNATURES:
                if signature in text:
                    company.ats = provider
                    return

    # -- helpers ------------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.request_timeout,
            follow_redirects=True,
            headers={"user-agent": _UA, "accept-language": "en-AU,en;q=0.9"},
        )

    @staticmethod
    def _is_live(client: httpx.Client, url: str) -> bool:
        try:
            return client.get(url).status_code == 200
        except httpx.HTTPError:
            return False


def _now() -> datetime:
    return datetime.now()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", name.lower())
    return slug


def _absolutize(base: str, href: str) -> str | None:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        m = re.match(r"(https?://[^/]+)", base)
        return f"{m.group(1)}{href}" if m else None
    return None