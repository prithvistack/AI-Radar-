"""GitHub: newly created repos rising fast in AI topics, and major/minor releases of a watchlist."""
from __future__ import annotations

import os
import re
from datetime import timedelta

from .common import get, make_item, now_utc, parse_date

API = "https://api.github.com"
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_repos(src: dict, ctx: dict) -> list[dict]:
    since = (now_utc() - timedelta(days=src.get("created_within_days", 7))).date().isoformat()
    repos = {}
    for topic in src["topics"]:
        q = f"topic:{topic} created:>={since} stars:>={src.get('min_stars', 100)}"
        data = get(f"{API}/search/repositories", headers=_headers(),
                   params={"q": q, "sort": "stars", "order": "desc", "per_page": src.get("per_topic", 15)}).json()
        for r in data.get("items", []):
            repos.setdefault(r["full_name"], r)
    items = []
    for r in repos.values():
        items.append(make_item(
            src,
            title=f"{r['full_name']}: {r.get('description') or 'new repository'}",
            url=r["html_url"],
            published=r["created_at"],
            raw_summary=r.get("description") or "",
            extra={"stars": r["stargazers_count"], "language": r.get("language"),
                   "topics": r.get("topics", [])[:6]},
        ))
    return items


def is_major_or_minor(tag: str) -> bool:
    m = _SEMVER.match(tag.strip())
    return bool(m) and int(m.group(3) or 0) == 0


def fetch_releases(src: dict, ctx: dict) -> list[dict]:
    cutoff = now_utc() - timedelta(days=src.get("max_age_days", 7))
    items = []
    for repo in src["repos"]:
        try:
            releases = get(f"{API}/repos/{repo}/releases", headers=_headers(), params={"per_page": 10}).json()
        except Exception as exc:
            print(f"  [GitHub Releases] {repo} failed: {exc}")
            continue
        for rel in releases:
            published = parse_date(rel.get("published_at"))
            if rel.get("draft") or rel.get("prerelease") or not published or published < cutoff:
                continue
            if not is_major_or_minor(rel.get("tag_name", "")):
                continue
            name = repo.split("/")[1]
            items.append(make_item(
                src,
                title=f"{name} {rel['tag_name']} released" + (f": {rel['name']}" if rel.get("name") and rel["name"] != rel["tag_name"] else ""),
                url=rel["html_url"],
                published=published,
                raw_summary=rel.get("body") or "",
                extra={"repo": repo, "tag": rel["tag_name"]},
            ))
    return items
