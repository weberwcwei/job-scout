"""Tests for config quality validation."""

from __future__ import annotations

from job_scout.config import AppConfig, validate_quality


def _cfg(**overrides) -> AppConfig:
    """Build a valid AppConfig with sensible defaults, applying overrides."""
    raw = {
        "profile": {
            "name": "Weber Wei",
            "target_title": "Software Engineer",
            "keywords": {
                "critical": ["python", "backend"],
                "strong": ["distributed"],
                "moderate": ["aws"],
                "weak": ["docker"],
            },
            "target_companies": {"tier1": ["Google"]},
            "title_signals": [{"pattern": "engineer", "points": 15}],
        },
        "search": {"terms": ["software engineer"], "locations": ["San Francisco"]},
        "scoring": {"min_alert_score": 55, "min_display_score": 20},
    }
    # Apply nested overrides
    for key, val in overrides.items():
        parts = key.split(".")
        d = raw
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = val
    return AppConfig(**raw)


class TestPlaceholderWarnings:
    def test_placeholder_name(self):
        cfg = _cfg(**{"profile.name": "Your Name"})
        diags = validate_quality(cfg)
        assert any(d.field == "profile.name" and d.level == "warning" for d in diags)

    def test_empty_name(self):
        cfg = _cfg(**{"profile.name": ""})
        diags = validate_quality(cfg)
        assert any(d.field == "profile.name" and d.level == "warning" for d in diags)

    def test_placeholder_target_title(self):
        cfg = _cfg(**{"profile.target_title": "your target job title"})
        diags = validate_quality(cfg)
        assert any(
            d.field == "profile.target_title" and d.level == "warning" for d in diags
        )

    def test_empty_search_terms(self):
        cfg = _cfg(**{"search.terms": []})
        diags = validate_quality(cfg)
        assert any(d.field == "search.terms" and d.level == "warning" for d in diags)

    def test_whitespace_only_search_terms(self):
        cfg = _cfg(**{"search.terms": ["  ", "\t"]})
        diags = validate_quality(cfg)
        assert any(d.field == "search.terms" and d.level == "warning" for d in diags)

    def test_empty_search_locations(self):
        cfg = _cfg(**{"search.locations": []})
        diags = validate_quality(cfg)
        assert any(
            d.field == "search.locations" and d.level == "warning" for d in diags
        )

    def test_whitespace_only_search_locations(self):
        cfg = _cfg(**{"search.locations": ["  "]})
        diags = validate_quality(cfg)
        assert any(
            d.field == "search.locations" and d.level == "warning" for d in diags
        )


class TestKeywordWarnings:
    def test_all_keyword_tiers_empty(self):
        cfg = _cfg(
            **{
                "profile.keywords": {
                    "critical": [],
                    "strong": [],
                    "moderate": [],
                    "weak": [],
                },
            }
        )
        diags = validate_quality(cfg)
        assert any(
            d.field == "profile.keywords" and "No keywords defined" in d.message
            for d in diags
        )

    def test_critical_empty_only_strong(self):
        """Strong-only with no critical: keyword score will be 0 (strong is ignored by gate)."""
        cfg = _cfg(
            **{
                "profile.keywords": {
                    "critical": [],
                    "strong": ["distributed"],
                    "moderate": [],
                    "weak": [],
                },
            }
        )
        diags = validate_quality(cfg)
        assert any(
            d.field == "profile.keywords"
            and "strong" in d.message.lower()
            and "critical" in d.message.lower()
            for d in diags
        )

    def test_critical_empty_moderate_populated(self):
        """No critical but moderate/weak present: capped at 10."""
        cfg = _cfg(
            **{
                "profile.keywords": {
                    "critical": [],
                    "strong": [],
                    "moderate": ["aws"],
                    "weak": ["docker"],
                },
            }
        )
        diags = validate_quality(cfg)
        assert any(
            d.field == "profile.keywords.critical" and "capped at 10" in d.message
            for d in diags
        )

    def test_critical_empty_weak_populated(self):
        cfg = _cfg(
            **{
                "profile.keywords": {
                    "critical": [],
                    "strong": [],
                    "moderate": [],
                    "weak": ["docker"],
                },
            }
        )
        diags = validate_quality(cfg)
        assert any(
            d.field == "profile.keywords.critical" and "capped at 10" in d.message
            for d in diags
        )


class TestScoringWarnings:
    def test_unreachable_min_alert_score(self):
        """min_alert_score higher than max achievable should warn."""
        # Config with no companies, no title signals: max = 10 (recency) + 55 (keywords) = 65
        cfg = _cfg(
            **{
                "profile.target_companies": {"tier1": [], "tier2": [], "tier3": []},
                "profile.title_signals": [],
                "scoring.min_alert_score": 80,
            }
        )
        diags = validate_quality(cfg)
        assert any(
            d.field == "scoring.min_alert_score" and "unreachable" in d.message.lower()
            for d in diags
        )

    def test_strong_only_max_score_excludes_keywords(self):
        """Strong-only config: keyword contribution is 0, so max is lower."""
        cfg = _cfg(
            **{
                "profile.keywords": {
                    "critical": [],
                    "strong": ["python"],
                    "moderate": [],
                    "weak": [],
                },
                "profile.target_companies": {
                    "tier1": ["Google"],
                    "tier2": [],
                    "tier3": [],
                },
                "profile.title_signals": [{"pattern": "engineer", "points": 15}],
                "scoring.min_alert_score": 50,
            }
        )
        # max = 10 (recency) + 0 (strong-only, no keywords) + 15 (company) + 20 (title) = 45
        diags = validate_quality(cfg)
        assert any(
            d.field == "scoring.min_alert_score" and "unreachable" in d.message.lower()
            for d in diags
        )

    def test_min_alert_below_min_display(self):
        cfg = _cfg(
            **{
                "scoring.min_alert_score": 15,
                "scoring.min_display_score": 20,
            }
        )
        diags = validate_quality(cfg)
        assert any(
            d.field == "scoring.min_alert_score"
            and "below min_display_score" in d.message
            for d in diags
        )


class TestDealBreakerRegexErrors:
    def test_invalid_title_pattern(self):
        cfg = _cfg(
            **{
                "profile.dealbreakers": {
                    "title_patterns": ["*intern*"],
                    "company_patterns": [],
                    "description_patterns": [],
                },
            }
        )
        diags = validate_quality(cfg)
        assert any(
            d.field == "profile.dealbreakers.title_patterns"
            and d.level == "error"
            and "*intern*" in d.message
            for d in diags
        )

    def test_invalid_company_pattern(self):
        cfg = _cfg(
            **{
                "profile.dealbreakers": {
                    "title_patterns": [],
                    "company_patterns": ["[bad"],
                    "description_patterns": [],
                },
            }
        )
        diags = validate_quality(cfg)
        assert any(
            d.field == "profile.dealbreakers.company_patterns" and d.level == "error"
            for d in diags
        )

    def test_invalid_description_pattern(self):
        cfg = _cfg(
            **{
                "profile.dealbreakers": {
                    "title_patterns": [],
                    "company_patterns": [],
                    "description_patterns": ["(unclosed"],
                },
            }
        )
        diags = validate_quality(cfg)
        assert any(
            d.field == "profile.dealbreakers.description_patterns"
            and d.level == "error"
            for d in diags
        )

    def test_valid_regex_no_error(self):
        cfg = _cfg(
            **{
                "profile.dealbreakers": {
                    "title_patterns": [r"(?i)\bintern\b"],
                    "company_patterns": [r"staffing.*agency"],
                    "description_patterns": [r"no\s+remote"],
                },
            }
        )
        diags = validate_quality(cfg)
        assert not any(d.level == "error" for d in diags)


class TestCleanConfig:
    def test_fully_populated_config_no_diagnostics(self):
        """A well-configured config should produce zero diagnostics."""
        cfg = _cfg()
        diags = validate_quality(cfg)
        assert diags == []
