"""Discovery source URLs and shared extraction helpers."""

from __future__ import annotations

import re

VC_PORTFOLIO_URLS: dict[str, str] = {
    "blackbird": "https://www.blackbird.vc/portfolio",
    "airtree": "https://www.airtree.vc/companies",  # JS-rendered; low yield
    "squarepeg": "https://squarepeg.com.au/portfolio",  # DNS dead as of 2026-08
    "mainsequence": "https://www.mseq.vc/about",  # portfolio redirects to /about
    "startmate": "https://www.startmate.com/portfolio",  # JS-rendered; low yield
}

FUNDING_RSS_URLS: dict[str, str] = {
    "startupdaily": "https://www.startupdaily.net/feed/",
    "smartcompany": "https://www.smartcompany.com.au/feed/",
    "techcrunch_au": "https://techcrunch.com/tag/australia/feed/",
}

#: Domains an ATS hosts its boards on, used for `site:` search queries.
ATS_DOMAINS = (
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
)

#: Search terms joined into a single DuckDuckGo `site:` query.
ATS_SEARCH_QUERY = " OR ".join(f"site:{d}" for d in ATS_DOMAINS)

#: DuckDuckGo HTML endpoint (no key, no JS).
DDG_HTML_URL = "https://html.duckduckgo.com/html/"

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "for", "of", "in", "on", "to", "with",
    "at", "by", "is", "are", "was", "as", "co", "ltd", "pty", "inc",
    "home", "about", "contact", "careers", "jobs", "blog", "news", "media",
    "privacy", "terms", "portfolio", "companies", "team", "insights",
    "faq", "login", "sign", "search", "menu", "events", "merch", "writing",
    "invest", "mentors", "operators", "founders", "accelerator", "programs",
    "network", "open source", "get in touch", "explore all", "read more",
    "brand kit", "code of conduct", "our story", "student founder",
    "talent engine", "first believers", "partner with us", "scroll down",
    "load more", "lp faqs", "lp portal", "linkedin", "twitter", "instagram",
    "facebook", "youtube", "medium", "x",
}


def clean_company_name(text: str) -> str:
    """Trim whitespace/artefacts from a scraped company name."""
    return re.sub(r"\s+", " ", text.strip())


def extract_domain(url: str) -> str:
    """Return a bare hostname without www. prefix, or empty string."""
    m = re.search(r"https?://([^/]+)/?", url)
    if not m:
        return ""
    domain = m.group(1).lower()
    domain = re.sub(r"^www\.", "", domain)
    return domain


def looks_like_company(text: str) -> bool:
    """Heuristic: is this scraped anchor a plausible company name?"""
    name = clean_company_name(text)
    if not name or len(name) > 60:
        return False
    words = [w.strip(".,;:()[]") for w in name.split()]
    if not words:
        return False
    lowered_words = [w.lower() for w in words]
    # Reject pure nav/utility labels (whole-name or whole-token match).
    if " ".join(lowered_words) in _STOP_WORDS:
        return False
    if any(w in _STOP_WORDS for w in lowered_words):
        return False
    # Reject doubled phrases ("Accelerator Accelerator", "Read more Read more",
    # "First Believers First Believers", "explore all explore all").
    if len(words) >= 2:
        half = len(lowered_words) // 2
        if lowered_words[:half] == lowered_words[half : half + half]:
            return False
    # Reject sentences/descriptions with punctuation or long runs.
    if any(all(c.isdigit() or c in "+$-." for c in w) for w in words):
        return False
    # Reject strings that contain a description (e.g. "Eggy Eggy is life...").
    if len(words) >= 5:
        return False
    return True


_FUNDING_VERBS = (
    "raises", "raised", "raises", "closes", "closed", "secures", "secured",
    "lands", "landed", "banks", "snags", "locks", "locks in", "wins",
    "announces", "launches", "scores",
)


def company_from_headline(title: str) -> str | None:
    """Guess a company name from a funding-news headline."""
    cleaned = clean_company_name(title)
    if not cleaned:
        return None
    # Strip a leading source/site tag like "[Funding]".
    cleaned = re.sub(r"^\[[^\]]*\]\s*", "", cleaned)
    # Find the first separator: funding verbs, dollar amounts, or punctuation.
    lowered = cleaned.lower()
    split_idx = len(cleaned)
    for verb in _FUNDING_VERBS:
        idx = lowered.find(verb)
        if 0 < idx < split_idx:
            split_idx = idx
    # Also break on a dollar figure like "$50m".
    money = re.search(r"\$\d", cleaned)
    if money and money.start() < split_idx:
        split_idx = money.start()
    candidate = cleaned[:split_idx].strip(" :–—-")
    candidate = re.sub(r"\s+(raises?|closes?|secures?|lands?)$", "", candidate)
    if not candidate or len(candidate) > 60:
        return None
    return candidate