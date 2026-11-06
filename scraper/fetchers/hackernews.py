"""Hacker News via the Algolia API: popular stories from the last day (AI keyword filter applied later).

The query is left empty on purpose: Algolia's typo tolerance turns "LLM" into matches like "Lemmings",
so we pull every high-scoring story and let the shared keyword prefilter decide.
"""
from __future__ import annotations

import time

from .common import get, make_item


def fetch(src: dict, ctx: dict) -> list[dict]:
    since = int(time.time()) - src.get("hours", 24) * 3600
    data = get("https://hn.algolia.com/api/v1/search", params={
        "tags": "story",
        "numericFilters": f"points>{src.get('min_points', 100)},created_at_i>{since}",
        "hitsPerPage": 200,
    }).json()
    items = []
    for h in data.get("hits", []):
        hn_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
        items.append(make_item(
            src,
            title=h.get("title", ""),
            url=h.get("url") or hn_url,
            published=h.get("created_at_i"),
            raw_summary=h.get("story_text") or "",
            extra={"hn_url": hn_url, "points": h.get("points", 0), "comments": h.get("num_comments", 0)},
        ))
    return items
