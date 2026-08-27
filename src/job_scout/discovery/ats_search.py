"""ATS-domain search discovery: find board slugs via a search engine.

Best-effort. Uses the DuckDuckGo HTML endpoint (no key, no JS) with a
`site:` query over Greenhouse/Lever/Ashby board domains. Results are parsed
for board URLs; the tenant slug is extracted and returned as a partial
company (name = slug, ats set, slug set, provenance ``ats_search``).

Because there is no free official search API, this degrades gracefully: any
HTTP error, block, or rate limit returns [] and logs.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from job_scout.discovery.base import DiscoverySource
from job_scout.discovery.constants import DDG_HTML_URL, ATS_SEARCH_QUERY
from job_scout.models import ATSProvider, Company

log = logging.getLogger("job_scout.discovery.ats_search")

#: Map a board host to (ATSProvider, slug extraction regex).
_BOARD_PATTERNS: list[tuple[str, ATSProvider, re.Pattern]] = [
    (
        "boards.greenhouse.io",
        ATSProvider.GREENHOUSE,
        re.compile(r"boards\.greenhouse\.io/([A-Za-z0-9_-]+)"),
    ),
    (
        "jobs.lever.co",
        ATSProvider.LEVER,
        re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"),
    ),
    (
        "jobs.ashbyhq.com",
        ATSProvider.ASHBY,
        re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)"),
    ),
]


class ATSSearchSource(DiscoverySource):
    name = "ats_search"

    def discover(self) -> list[Company]:
        if not self.config.ats_search_enabled:
            return []
        found: dict[tuple[ATSProvider, str], Company] = {}
        with self._client() as client:
            html = self._search(client, ATS_SEARCH_QUERY)
            if html is None:
                return []
            soup = BeautifulSoup(html, "html.parser")
            for result in soup.select(".result__a, a.result-link, a.result__url"):
                href = result.get("href")
                href = href.strip() if isinstance(href, str) else ""
                board = self._parse_board_url(self._unwrap_redirect(href))
                if board:
                    provider, slug = board
                    key = (provider, slug)
                    if key not in found:
                        found[key] = Company(
                            name=slug,
                            ats=provider,
                            ats_slug=slug,
                            discovered_from=["ats_search"],
                        )
        return list(found.values())

    @staticmethod
    def _unwrap_redirect(href: str) -> str:
        parts = urlsplit(href)
        if parts.netloc == "duckduckgo.com" and parts.path.startswith("/l/"):
            uddg = parse_qs(parts.query).get("uddg")
            if uddg:
                return unquote(uddg[0])
        return href

    def _search(self, client, query: str) -> str | None:
        return self._get_text(client, f"{DDG_HTML_URL}?q={query}")

    @staticmethod
    def _parse_board_url(href: str) -> tuple[ATSProvider, str] | None:
        for host, provider, pattern in _BOARD_PATTERNS:
            m = pattern.search(href)
            # Only accept genuinely board-shaped URLs (not careers marketing).
            if m:
                slug = m.group(1)
                if slug and slug.lower() not in ("jobs", "postings", "job-board"):
                    return provider, slug
        return None
