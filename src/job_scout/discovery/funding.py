"""Funding-news discovery: parse RSS feeds for freshly funded companies.

Each configured feed key maps to a URL in :data:`FUNDING_RSS_URLS`. Headlines
are heuristically parsed with :func:`company_from_headline` to yield a company
name; the company is tagged with the ``funding_news`` provenance plus the feed
key.
"""

from __future__ import annotations

import logging
from xml.etree import ElementTree

from job_scout.discovery.base import DiscoverySource
from job_scout.discovery.constants import FUNDING_RSS_URLS, company_from_headline
from job_scout.models import Company

log = logging.getLogger("job_scout.discovery.funding")


class FundingSource(DiscoverySource):
    name = "funding"

    def discover(self) -> list[Company]:
        companies: dict[str, list[str]] = {}
        with self._client() as client:
            for source in self.config.funding_sources:
                url = FUNDING_RSS_URLS.get(source)
                if not url:
                    log.warning("unknown funding source %r; skipped", source)
                    continue
                text = self._get_text(client, url)
                if text is None:
                    continue
                names = self._extract_names(text, source)
                for name in names:
                    companies.setdefault(name, ["funding_news", source])
                self._delay()

        return [
            Company(name=name, discovered_from=provenance)
            for name, provenance in companies.items()
        ]

    @staticmethod
    def _extract_names(xml_text: str, source: str) -> list[str]:
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as e:
            log.warning("funding: bad XML from %s: %s", source, e)
            return []
        names: list[str] = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is None or not title_el.text:
                continue
            company = company_from_headline(title_el.text)
            if company:
                names.append(company)
        seen: set[str] = set()
        result: list[str] = []
        for name in names:
            if name.lower() not in seen:
                seen.add(name.lower())
                result.append(name)
        return result