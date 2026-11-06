"""Reddit top-of-day posts. Optional: Reddit blocks most cloud IPs, so failures are expected."""
from __future__ import annotations

from .common import get, make_item


def fetch(src: dict, ctx: dict) -> list[dict]:
    items = []
    for sub in src["subreddits"]:
        data = get(f"https://www.reddit.com/r/{sub}/top/.json", params={"t": "day", "limit": 25}).json()
        for child in data.get("data", {}).get("children", []):
            p = child["data"]
            if p.get("score", 0) < src.get("min_score", 100) or p.get("stickied"):
                continue
            permalink = f"https://www.reddit.com{p['permalink']}"
            url = permalink if p.get("is_self") else p.get("url") or permalink
            items.append(make_item(
                src,
                title=p.get("title", ""),
                url=url,
                published=p.get("created_utc"),
                raw_summary=p.get("selftext", ""),
                source_name=f"r/{sub}",
                extra={"reddit_url": permalink, "points": p.get("score", 0)},
            ))
    return items
