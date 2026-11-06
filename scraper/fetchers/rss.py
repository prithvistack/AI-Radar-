"""Generic RSS/Atom fetcher, with Google News handling (publisher names, title suffixes)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

import feedparser

from .common import cover_url, get, make_item

INLINE_IMG = re.compile(r"<img[^>]+src=[\"']([^\"']+)", re.I)


def entry_image(entry) -> str:
    """Image carried by a feed entry, if any."""
    found = [m.get("url") for m in entry.get("media_content", []) if m.get("medium", "image") == "image"]
    found += [m.get("url") for m in entry.get("media_thumbnail", [])]
    found += [e.get("href") for e in entry.get("enclosures", []) if e.get("type", "").startswith("image/")]
    html = " ".join([entry.get("summary", "")] + [c.get("value", "") for c in entry.get("content", [])])
    found += INLINE_IMG.findall(html)
    return next(filter(None, map(cover_url, found)), "")


def fetch(src: dict, ctx: dict) -> list[dict]:
    feed = feedparser.parse(get(src["url"]).content)
    exclude = re.compile(src["exclude_title"], re.I) if src.get("exclude_title") else None
    items = []
    for e in feed.entries:
        title = e.get("title", "")
        link = e.get("link", "")
        if not title or not link or (exclude and exclude.search(title)):
            continue
        summary = e.get("summary") or e.get("description") or ""
        source_name = None
        if src.get("google_news"):
            publisher = (e.get("source") or {}).get("title")
            if publisher:
                suffix = f" - {publisher}"
                if title.endswith(suffix):
                    title = title[: -len(suffix)]
                # Some publishers have no name, only a URL: show the bare host.
                if publisher.startswith("http"):
                    publisher = urlparse(publisher).hostname or publisher
                source_name = publisher.removeprefix("www.")
            # Google News summaries are just the title + publisher again.
            summary = ""
        needle = src.get("require_keyword")
        if needle and needle.lower() not in f"{title} {e.get('summary', '')}".lower():
            continue
        published = e.get("published_parsed") or e.get("updated_parsed") or e.get("published")
        items.append(make_item(src, title=title, url=link, published=published,
                               raw_summary=summary, source_name=source_name, image=entry_image(e)))
    return items
