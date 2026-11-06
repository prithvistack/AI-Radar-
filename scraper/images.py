"""Find a cover image for each story: the feed's own image, else the publisher's og:image.

Only image URLs are stored; the browser loads them from the publisher. Blocked sites (Reuters) get another
chance through other outlets on the same story, and arXiv papers use their first figure.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from fetchers import common
from fetchers.html_links import _meta

MAX_TRIES = 2
MAX_PAGES_PER_STORY = 3
GENERIC_REUSE = 3  # the same picture on this many stories is a site default, not a cover


def from_soup(soup: BeautifulSoup, page_url: str) -> str:
    image = _meta(soup, "og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src")
    return common.cover_url(urljoin(page_url, image)) if image else ""


def from_page(url: str) -> str:
    """The publisher's share image, or "" if the page is blocked or has none."""
    try:
        resp = common.get(url, timeout=10)
    except requests.RequestException:
        return ""
    if "html" not in resp.headers.get("Content-Type", ""):
        return ""
    return from_soup(BeautifulSoup(resp.text, "html.parser"), resp.url)


def from_arxiv(arxiv_id: str) -> str:
    """A paper's first figure, from its HTML version (older papers have none)."""
    url = f"https://arxiv.org/html/{arxiv_id}"
    try:
        resp = common.get(url, timeout=10)
    except requests.RequestException:
        return ""
    img = BeautifulSoup(resp.text, "html.parser").select_one("figure img[src]")
    if not img:
        return ""
    # Figure paths usually start with the versioned id ("2611.0001v1/fig.png"), relative to /html/.
    base = "https://arxiv.org/html/" if "/" in img["src"] else resp.url + "/"
    return common.cover_url(urljoin(base, img["src"]))


def find(story: dict) -> tuple[str, str]:
    """(image URL, the page it came from), so the picture can be credited to the right outlet."""
    arxiv_id = (story.get("extra") or {}).get("arxiv_id")
    if arxiv_id and (image := from_arxiv(arxiv_id)):
        return image, ""
    urls = dict.fromkeys([story["url"]] + [s["url"] for s in story.get("sources", [])])
    for url in list(urls)[:MAX_PAGES_PER_STORY]:
        if image := from_page(url):
            return image, url
    return "", ""


def drop_generic(stories: list[dict]) -> int:
    """Forget images shared by many stories: sites without a real picture often serve one default."""
    uses = Counter(s["image"] for s in stories if s.get("image"))
    repeated = {image for image, n in uses.items() if n >= GENERIC_REUSE}
    for s in stories:
        if s.get("image") in repeated:
            s["image"] = ""
            s["image_tries"] = MAX_TRIES  # the page will only give the same picture again
    return len(repeated)


def backfill(stories: list[dict], cfg: dict) -> int:
    """Look up images for stories missing one, most important first. Returns how many were found."""
    todo = [s for s in stories if not s.get("image") and s.get("image_tries", 0) < MAX_TRIES]
    todo = sorted(todo, key=lambda s: s.get("score", 0), reverse=True)[: cfg["images"].get("max_per_run", 150)]

    def fill(story):
        story["image_tries"] = story.get("image_tries", 0) + 1
        story["image"], story["image_page"] = find(story)

    with ThreadPoolExecutor(max_workers=cfg["images"].get("workers", 6)) as pool:
        list(pool.map(fill, todo))
    drop_generic(stories)
    return sum(1 for s in todo if s.get("image"))
