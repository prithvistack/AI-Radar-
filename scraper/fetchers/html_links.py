"""For sites with no RSS: diff the links on an index page against the links seen last run.

Unseen links are treated as new posts; each post page is fetched for title, summary and date.
On the very first run for a source, every link is recorded but only the first few are emitted.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .common import clean_text, get, make_item

FIRST_RUN_EMIT = 10  # older posts among these are dropped by the age filter
MAX_NEW_PER_RUN = 8


def _index_links(src: dict) -> list[str]:
    soup = BeautifulSoup(get(src["url"]).text, "html.parser")
    pattern = re.compile(src["link_pattern"])
    exclude = re.compile(src["exclude_pattern"]) if src.get("exclude_pattern") else None
    index_path = urlparse(src["url"]).path.rstrip("/")
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0].split("?")[0]
        path = urlparse(urljoin(src["url"], href)).path
        if path.rstrip("/") == index_path or not pattern.search(path):
            continue
        if exclude and exclude.search(path):
            continue
        full = urljoin(src["url"], path)
        if full not in seen:
            seen.add(full)
            links.append(full)
    return links


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _published(soup: BeautifulSoup, raw_html: str) -> str:
    date = _meta(soup, "article:published_time", "og:published_time", "date")
    if date:
        return date
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', raw_html)
    if m:
        return m.group(1)
    t = soup.find("time", attrs={"datetime": True})
    return t["datetime"] if t else ""


def _first_paragraph(soup: BeautifulSoup) -> str:
    root = soup.find("article") or soup.find("main") or soup
    for p in root.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) >= 80:
            return text
    return ""


def _post(src: dict, url: str) -> dict | None:
    raw = get(url).text
    soup = BeautifulSoup(raw, "html.parser")
    title = _meta(soup, "og:title") or (soup.title.string if soup.title else "") or url
    for suffix in (f" | {src['name']}", f" \\ {src['name']}", f" - {src['name']}"):
        title = title.replace(suffix, "")
    # Prefer real body text: some sites reuse one boilerplate meta description on every page.
    summary = _first_paragraph(soup) or _meta(soup, "og:description", "description")
    published = _published(soup, raw)
    if not published:
        return None  # index/category pages have no publish date; real posts do
    return make_item(src, title=title, url=url, published=published,
                     raw_summary=clean_text(summary, 500))


def fetch(src: dict, ctx: dict) -> list[dict]:
    known_all = ctx["state"].setdefault("html_known", {})
    known = set(known_all.get(src["name"], []))
    links = _index_links(src)
    if not links:
        raise RuntimeError(f"no links matched {src['link_pattern']} on {src['url']}")
    new = [u for u in links if u not in known]
    new = new[:FIRST_RUN_EMIT] if not known else new[:MAX_NEW_PER_RUN]
    # Remember everything on the page (bounded) so old posts are never re-emitted.
    known_all[src["name"]] = list(dict.fromkeys(links + list(known)))[:500]

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [item for item in pool.map(lambda u: _safe_post(src, u), new) if item]


def _safe_post(src: dict, url: str) -> dict | None:
    try:
        return _post(src, url)
    except Exception as exc:  # one bad post page shouldn't drop the rest
        print(f"  [{src['name']}] post failed {url}: {exc}")
        return None

