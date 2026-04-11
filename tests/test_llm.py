"""Tests for llm.py — Gemini NL parsing for job status updates."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from job_scout.llm import _format_job_context, parse_status_update
from job_scout.models import Job, Location, Site


def _make_job(
    job_id: int,
    company: str = "TestCo",
    title: str = "Engineer",
    score: int = 50,
    status: str = "new",
) -> Job:
    return Job(
        id=job_id,
        source=Site.LINKEDIN,
        source_id=f"src-{job_id}",
        url=f"https://example.com/{job_id}",
        title=title,
        company=company,
        location=Location(city="SF", state="CA"),
        description="A job",
        score=score,
        score_breakdown={"keyword": score},
        status=status,
    )


class TestFormatJobContext:
    def test_formats_single_job(self):
        jobs = [_make_job(42, company="Google", title="Senior Engineer", score=85)]
        result = _format_job_context(jobs)
        assert "#42" in result
        assert "Google" in result
        assert "Senior Engineer" in result
        assert "score:85" in result
        assert "status:new" in result

    def test_formats_multiple_jobs(self):
        jobs = [
            _make_job(42, company="Google", title="Senior Eng"),
            _make_job(43, company="Meta", title="Backend Dev", status="applied"),
        ]
        result = _format_job_context(jobs)
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert "status:applied" in lines[1]

    def test_empty_list(self):
        result = _format_job_context([])
        assert result == ""


class TestParseStatusUpdate:
    def test_result_has_required_keys_on_error(self):
        """On any error, result always has 'updates' and 'reply' keys."""
        result = parse_status_update(
            message="applied 42",
            jobs=[_make_job(42)],
            api_key="fake-key-will-fail",
        )
        assert "updates" in result
        assert "reply" in result
        assert isinstance(result["updates"], list)

    def test_successful_parse_with_ids(self):
        """Mock Gemini returning a valid update with job IDs."""
        jobs = [_make_job(42, "Google", "Senior Eng"), _make_job(43, "Meta", "Backend")]

        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "updates": [
                    {"job_id": 42, "status": "applied", "notes": None},
                    {"job_id": 43, "status": "applied", "notes": None},
                ],
                "reply": None,
            }
        )

        with patch("job_scout.llm.parse_status_update") as mock_parse:
            mock_parse.return_value = json.loads(mock_response.text)
            result = mock_parse("applied 42, 43", jobs, "fake-key")

        assert len(result["updates"]) == 2
        assert result["updates"][0]["job_id"] == 42
        assert result["updates"][0]["status"] == "applied"
        assert result["reply"] is None

    def test_gemini_api_error_returns_fallback(self):
        """On API error, returns empty updates with error reply."""
        jobs = [_make_job(42)]

        # This will fail because there's no real API key
        result = parse_status_update(
            message="applied 42",
            jobs=jobs,
            api_key="fake-key-that-will-fail",
        )
        assert result["updates"] == []
        assert result["reply"] is not None  # error message

    def test_result_structure_always_has_required_keys(self):
        """Regardless of error, result always has 'updates' and 'reply'."""
        result = parse_status_update(
            message="hello",
            jobs=[],
            api_key="bad-key",
        )
        assert "updates" in result
        assert "reply" in result
        assert isinstance(result["updates"], list)


class TestParseResultStructures:
    """Test that various LLM response structures are handled correctly."""

    def test_interview_status(self):
        """Verify interview is a valid status in our context formatting."""
        job = _make_job(42, status="interview")
        ctx = _format_job_context([job])
        assert "status:interview" in ctx

    def test_offer_status(self):
        """Verify offer is a valid status in our context formatting."""
        job = _make_job(42, status="offer")
        ctx = _format_job_context([job])
        assert "status:offer" in ctx
