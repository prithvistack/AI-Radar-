"""URL canonicalization and story clustering.

A cluster is a feed Item: one primary link plus every source that covered the same story.
New items join an existing cluster when their canonical URL matches one of its links, or when
their title is similar enough (rapidfuzz token_set_ratio) to one of its titles within the window.
"""
from __future__ import annotations

import base64
import hashlib
import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from rapidfuzz import fuzz

from fetchers.common import now_utc, iso, parse_date

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "ref_url",
    "cmpid", "smid", "ocid", "taid", "guccounter", "guce_referrer", "guce_referrer_sig", "_hsenc",
    "_hsmi", "mkt_tok", "oc", "rss", "feed", "src", "sr_share", "share", "cid", "spm", "s_cid",
}
# Titles from these source types are templated ("X is trending", "Y v2.0 released"), so only URLs cluster them.
URL_ONLY_TYPES = {"model", "repo", "release"}


def decode_google_news(url: str) -> str:
    """Old-style Google News article IDs are base64 protobufs that embed the publisher URL.
    Newer IDs are opaque; those are returned unchanged and clustered by title instead."""
    m = re.match(r"https?://news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_-]+)", url)
    if not m:
        return url
    token = m.group(1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return url
    found = re.search(rb"https?://[\x21-\x7e]+", raw)
    return found.group(0).decode("ascii") if found else url


def canonicalize_url(url: str) -> str:
    url = decode_google_news(url.strip())
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if p.port and p.port not in (80, 443):
        host = f"{host}:{p.port}"
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if host == "arxiv.org":
        m = re.match(r"^/(?:abs|pdf|html)/([^/]+?)(?:v\d+)?(?:\.pdf)?/?$", path)
        if m:
            path = f"/abs/{m.group(1)}"
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted(
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    )
    return urlunparse(("https", host, path, "", urlencode(query), ""))


def url_id(url: str) -> str:
    return hashlib.sha1(canonicalize_url(url).encode()).hexdigest()


def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[‘’“”'\"]", "", title)
    return re.sub(r"[^\w\s.+-]", " ", title).strip()


def titles_match(a: str, b: str, threshold: int, min_tokens: int) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    if min(len(na.split()), len(nb.split())) < min_tokens:
        return na == nb
    return fuzz.token_set_ratio(na, nb) >= threshold


def source_entry(item: dict) -> dict:
    entry = {"name": item["source_name"], "url": item["url"], "title": item["title"],
             "authority": item["authority"]}
    discussion = item.get("extra", {}).get("hn_url") or item.get("extra", {}).get("reddit_url")
    if discussion and discussion != item["url"]:
        entry["discussion"] = discussion
    return entry


def new_cluster(item: dict) -> dict:
    return {
        "id": url_id(item["url"]),
        "title": item["title"],
        "url": item["url"],
        "sources": [source_entry(item)],
        "source_type": item["source_type"],
        "published": item["published"],
        "fetched": iso(now_utc()),
        "raw_summary": item["raw_summary"],
        "summary": "",
        "category": "other",
        "significance": 0,
        "substance": False,
        "hype": False,
        "key_terms": [],
        "score": 0.0,
        "extra": item.get("extra", {}),
        "image": item.get("image", ""),
        "enriched": False,
    }


def add_source(cluster: dict, item: dict) -> bool:
    """Attach item to cluster. Returns False if that outlet is already listed.
    Different outlets sharing one URL (HN linking to a lab post) count as separate sources."""
    if any(s["name"] == item["source_name"] for s in cluster["sources"]):
        return False  # same outlet twice (e.g. via Google News and its own feed)
    cluster["sources"].append(source_entry(item))
    cluster["image"] = cluster.get("image") or item.get("image", "")
    primary_auth = max((s.get("authority", 0) for s in cluster["sources"][:-1]), default=0)
    # A more authoritative source (e.g. the lab's own post) becomes the primary link; the id stays stable.
    if item["authority"] > primary_auth:
        cluster["title"], cluster["url"] = item["title"], item["url"]
        cluster["source_type"] = item["source_type"]
        if item["raw_summary"]:
            cluster["raw_summary"] = item["raw_summary"]
        cluster["sources"].insert(0, cluster["sources"].pop())
        cluster["extra"] = {**cluster.get("extra", {}), **item.get("extra", {})}
    if item["published"] < cluster["published"]:
        cluster["published"] = item["published"]
    return True


def cluster_items(new_items: list[dict], existing: list[dict], limits: dict) -> tuple[list[dict], list[dict]]:
    """Merge new_items into existing clusters or create new ones.

    Returns (new_clusters, touched_existing_clusters)."""
    threshold = limits.get("title_similarity", 85)
    min_tokens = limits.get("min_title_tokens", 4)
    window = timedelta(hours=limits.get("cluster_window_hours", 48))

    clusters = list(existing)
    by_url: dict[str, dict] = {}
    for c in clusters:
        for s in c["sources"]:
            by_url[canonicalize_url(s["url"])] = c

    new_ids, touched = set(), set()
    created: list[dict] = []
    # Most authoritative and earliest first, so they become primaries.
    for item in sorted(new_items, key=lambda i: (-i["authority"], i["published"])):
        canon = canonicalize_url(item["url"])
        target = by_url.get(canon)
        if target is None and item["source_type"] not in URL_ONLY_TYPES:
            pub = parse_date(item["published"])
            for c in clusters:
                if c["source_type"] in URL_ONLY_TYPES:
                    continue
                if abs(parse_date(c["published"]) - pub) > window:
                    continue
                if any(titles_match(item["title"], s.get("title") or c["title"], threshold, min_tokens)
                       for s in c["sources"]):
                    target = c
                    break
        if target is None:
            c = new_cluster(item)
            clusters.append(c)
            created.append(c)
            new_ids.add(id(c))
            by_url[canon] = c
        else:
            if add_source(target, item):
                by_url[canon] = target
                if id(target) not in new_ids:
                    touched.add(id(target))
    return created, [c for c in existing if id(c) in touched]
