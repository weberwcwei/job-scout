"""Company registry and resolution.

Exposes :class:`~job_scout.registry.companies.CompanyResolver`, which fills in
missing company fields (domain, careers URL, ATS, slug) from the network.

See ATS_DISCOVERY.md (M3) for the resolution contract.
"""

from __future__ import annotations

from job_scout.registry.companies import CompanyResolver

__all__ = ["CompanyResolver"]