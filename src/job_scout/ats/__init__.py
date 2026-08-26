"""ATS adapter package: direct board polling for Greenhouse, Lever, Ashby.

Each adapter words the same way: fetch a board's public JSON, normalise each
posting into :class:`~job_scout.models.ATSJob`, and tag it with its resolved
company. The registry maps an :class:`~job_scout.models.ATSProvider` to its
adapter class.
"""

from __future__ import annotations

from job_scout.models import ATSProvider

from job_scout.ats.ashby import AshbyAdapter
from job_scout.ats.greenhouse import GreenhouseAdapter
from job_scout.ats.lever import LeverAdapter

_REGISTRY: dict[ATSProvider, type] = {
    ATSProvider.GREENHOUSE: GreenhouseAdapter,
    ATSProvider.LEVER: LeverAdapter,
    ATSProvider.ASHBY: AshbyAdapter,
}


def get_adapter(provider: ATSProvider):
    """Return the adapter class for a provider, or None if unsupported."""
    return _REGISTRY.get(provider)


def get_supported_providers() -> frozenset[ATSProvider]:
    return frozenset(_REGISTRY)