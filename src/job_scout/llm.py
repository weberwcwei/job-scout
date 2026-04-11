"""Gemini-powered natural language parsing for job status updates."""

from __future__ import annotations

import json
import logging

from job_scout.models import Job

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a job application status parser for a job-scout tool.
Given a user's Telegram message and their current job list, extract status updates.

Valid statuses: applied, interview, offer, rejected

Rules:
- Match jobs by ID (#42), company name, title, or description context.
- If multiple jobs could match a vague reference, pick the most likely one.
- If truly ambiguous, set reply to ask for clarification.
- "notes" captures any extra context the user provided (optional, null if none).

Return JSON matching this schema exactly:
{"updates": [{"job_id": <int>, "status": "<status>", "notes": <string or null>}], "reply": <string or null>}

- If you extracted updates successfully, "reply" should be null.
- If the message is ambiguous and you need clarification, return empty updates and set "reply" to your question.
- If the message is not a status update at all (greeting, random text), return: {"updates": [], "reply": null}
"""


def _format_job_context(jobs: list[Job]) -> str:
    """Format jobs as a compact list for the LLM prompt."""
    lines = []
    for job in jobs:
        loc = job.location.display if job.location else "Unknown"
        line = f"#{job.id} | {job.company} | {job.title} | {loc} | score:{job.score} | status:{job.status}"
        lines.append(line)
    return "\n".join(lines)


def parse_status_update(
    message: str,
    jobs: list[Job],
    api_key: str,
    model: str = "gemini-2.0-flash",
) -> dict:
    """Parse a natural language message into structured status updates.

    Returns {"updates": [...], "reply": str|None}.
    """
    try:
        from google import genai
    except ImportError:
        log.error(
            "google-genai not installed. Install with: pip install job-scout[bot]"
        )
        return {
            "updates": [],
            "reply": "Bot error: google-genai package not installed.",
        }

    job_context = _format_job_context(jobs)
    user_prompt = f"Job list:\n{job_context}\n\nUser message: {message}"

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )

        result = json.loads(response.text)
        if "updates" not in result:
            result["updates"] = []
        if "reply" not in result:
            result["reply"] = None
        return result
    except Exception as e:
        log.error(f"Gemini API error: {e}")
        return {
            "updates": [],
            "reply": "Sorry, I had trouble understanding that. Could you try again?",
        }
