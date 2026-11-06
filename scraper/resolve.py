"""Turn Google News redirect links into publisher URLs, and fill in summaries feeds didn't provide.

Google News RSS gives opaque links and no summary, so those stories can't be opened in the reader and
would otherwise show their headline twice. Resolution uses an undocumented Google endpoint: if it stops
working, stories simply keep their Google link and the run carries on.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from enrich import _truncate_words, same_text
from fetchers import common
from fetchers.html_links import _first_paragraph, _meta

GOOGLE_LINK = re.compile(r"https?://news\.google\.com/(?:rss/)?articles/([^?/]+)")
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
JUNK = re.compile(r"enable javascript|access denied|just a moment|are you a robot|verify you are human|"
                  r"cookies? (policy|settings)|page not found|subscribe to (read|continue)", re.I)
MIN_SUMMARY_CHARS = 40
MAX_TRIES = 2  # blocked sites (Reuters) stay blocked; don't refetch them every run


def is_google(url: str) -> bool:
    return bool(GOOGLE_LINK.match(url))


def decode_google_url(url: str) -> str | None:
    m = GOOGLE_LINK.match(url)
    if not m:
        return None
    article_id = m.group(1)
    try:
        page = common.get(f"https://news.google.com/rss/articles/{article_id}", timeout=10).text
        sig = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not sig or not ts:
            return None
        inner = json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None,
                                            None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                            article_id, int(ts.group(1)), sig.group(1)])
        resp = common.post(BATCH_URL, data="f.req=" + quote(json.dumps([[["Fbv4je", inner]]])),
                           headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=10)
        decoded = json.loads(json.loads(resp.text.split("\n\n", 1)[1])[0][2])[1]
        return decoded if isinstance(decoded, str) and decoded.startswith("http") else None
    except (requests.RequestException, ValueError, IndexError, KeyError, TypeError):
        return None


def page_summary(url: str, title: str) -> str:
    """The publisher's own description of the article, or "" if the page is blocked or has none."""
    try:
        resp = common.get(url, timeout=10)
    except requests.RequestException:
        return ""
    if "html" not in resp.headers.get("Content-Type", ""):
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    for text in (_meta(soup, "og:description", "description", "twitter:description"), _first_paragraph(soup)):
        text = common.clean_text(text, 500)
        if len(text) >= MIN_SUMMARY_CHARS and not JUNK.search(text) and not same_text(text, title):
            return text
    return ""


def _fix(url: str, title: str, summary: str) -> tuple[str, str]:
    real = decode_google_url(url) if is_google(url) else url
    if not real:
        return url, summary
    if same_text(summary, title):
        summary = page_summary(real, title)
    return real, summary


def resolve_new(items: list[dict], cfg: dict) -> int:
    """Fix freshly fetched items in place, newest first, up to the per-run cap."""
    todo = [i for i in items if is_google(i["url"]) or same_text(i.get("raw_summary"), i["title"])]
    todo = sorted(todo, key=lambda i: i["published"], reverse=True)[: cfg["resolve"].get("max_per_run", 200)]
    with ThreadPoolExecutor(max_workers=cfg["resolve"].get("workers", 4)) as pool:
        results = list(pool.map(lambda i: _fix(i["url"], i["title"], i.get("raw_summary", "")), todo))
    for item, (url, summary) in zip(todo, results):
        item["url"], item["raw_summary"] = url, summary
    return sum(1 for i in todo if not is_google(i["url"]))


def backfill(clusters: list[dict], cfg: dict) -> int:
    """Repair stories stored earlier: Google links, and summaries that only repeat the headline."""
    def needs_fix(c):
        return c.get("resolve_tries", 0) < MAX_TRIES and (
            any(is_google(s["url"]) for s in c["sources"]) or same_text(c.get("raw_summary"), c["title"]))

    todo = sorted(filter(needs_fix, clusters), key=lambda c: c["published"], reverse=True)
    todo = todo[: cfg["resolve"].get("max_per_run", 200)]

    def fix(c):
        c["resolve_tries"] = c.get("resolve_tries", 0) + 1
        real, summary = _fix(c["url"], c["title"], c.get("raw_summary", ""))
        for s in c["sources"]:
            if s["url"] == c["url"]:
                s["url"] = real
            elif is_google(s["url"]):
                s["url"] = decode_google_url(s["url"]) or s["url"]
        c["url"], c["raw_summary"] = real, summary
        if not c.get("enriched"):
            c["summary"] = _truncate_words(summary, 30) if not same_text(summary, c["title"]) else ""

    with ThreadPoolExecutor(max_workers=cfg["resolve"].get("workers", 4)) as pool:
        list(pool.map(fix, todo))
    return len(todo)
