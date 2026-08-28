"""VC portfolio discovery: scrape portfolio/companies pages for company names.

Each configured VC key maps to a URL in :data:`VC_PORTFOLIO_URLS`. This is a
light heuristic scraper: it collects plausible anchor text/company names from
the page and returns them as partial companies tagged with the VC's key.
"""

from __future__ import annotations

import logging

from job_scout.discovery.base import DiscoverySource
from job_scout.discovery.constants import (
    VC_PORTFOLIO_URLS,
    clean_company_name,
    looks_like_company,
)
from job_scout.models import Company

log = logging.getLogger("job_scout.discovery.vc_portfolios")


class VCPortfolioSource(DiscoverySource):
    name = "vc_portfolios"

    def discover(self) -> list[Company]:
        companies: dict[str, list[str]] = {}
        with self._client() as client:
            for vc in self.config.vc_portfolios:
                url = VC_PORTFOLIO_URLS.get(vc)
                if not url:
                    log.warning("unknown VC key %r; skipped", vc)
                    continue
                soup = self._get_html(client, url)
                if soup is None:
                    continue
                names = self._extract_names(soup)
                for name in names:
                    companies.setdefault(name, []).append(vc)
                self._delay()

        return [
            Company(name=name, discovered_from=provenance)
            for name, provenance in companies.items()
        ]

    @staticmethod
    def _extract_names(soup) -> list[str]:
        if soup is None:
            return []
        names: list[str] = []
        for anchor in soup.select("a"):
            text = anchor.get_text(" ", strip=True)
            if looks_like_company(text):
                names.append(clean_company_name(text))
        # Dedup preserving order.
        seen: set[str] = set()
        result: list[str] = []
        for name in names:
            if name.lower() not in seen:
                seen.add(name.lower())
                result.append(name)
        return result