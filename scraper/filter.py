"""AI-relevance keyword prefilter for general-news sources."""
from __future__ import annotations

import re


def build_matcher(keywords: list[str]) -> re.Pattern:
    alts = sorted((re.escape(k) for k in keywords), key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", re.IGNORECASE)


def is_relevant(item: dict, matcher: re.Pattern) -> bool:
    return bool(matcher.search(f"{item['title']} {item.get('raw_summary', '')}"))


def prefilter(items: list[dict], keywords: list[str]) -> list[dict]:
    matcher = build_matcher(keywords)
    return [it for it in items if not it.get("prefilter") or is_relevant(it, matcher)]
