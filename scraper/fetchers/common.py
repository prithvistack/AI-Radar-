"""Helpers shared by all fetchers: HTTP session, text cleanup, date parsing, item factory."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

_session: requests.Session | None = None
_timeout = 20


def configure(http_cfg: dict) -> None:
    global _session, _timeout
    _timeout = http_cfg.get("timeout_seconds", 20)
    _session = requests.Session()
    _session.headers["User-Agent"] = http_cfg["user_agent"]


def get(url: str, **kwargs) -> requests.Response:
    if _session is None:
        configure({"user_agent": "AI-Radar/1.0"})
    kwargs.setdefault("timeout", _timeout)
    resp = _session.get(url, **kwargs)
    resp.raise_for_status()
    return resp


def post(url: str, **kwargs) -> requests.Response:
    if _session is None:
        configure({"user_agent": "AI-Radar/1.0"})
    kwargs.setdefault("timeout", _timeout)
    resp = _session.post(url, **kwargs)
    resp.raise_for_status()
    return resp


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(text: str | None, limit: int = 500) -> str:
    if not text:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", text))
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


NOT_A_COVER = re.compile(r"logo|favicon|placeholder|default|sprite|avatar|blank|spacer|pixel|/icons?[/.-]|"
                         r"\.svg|\.gif|\.ico|/\.\w+$", re.I)
BAD_HOSTS = ("opengraph.githubassets.com", "repository-images.githubusercontent.com", "cdn-thumbnails.huggingface.co")


def cover_url(url: str | None) -> str:
    """The URL as https, or "" if it can't be a real cover."""
    url = (url or "").strip().replace("&amp;", "&")
    if url.startswith("http://"):
        url = "https://" + url[7:]
    host = urlparse(url).hostname or ""
    if not url.startswith("https://") or not host or host in BAD_HOSTS or NOT_A_COVER.search(url):
        return ""
    return url


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date(value) -> datetime | None:
    """Parse ISO 8601, RFC 822, unix timestamps or feedparser struct_time into aware UTC."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        if hasattr(value, "tm_year"):  # time.struct_time from feedparser (already UTC)
            return datetime(*value[:6], tzinfo=timezone.utc)
        s = str(value).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            s = s.replace("Z", "+00:00")
            if len(s) == 10:
                s += "T00:00:00+00:00"
            dt = datetime.fromisoformat(s)
        else:
            dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def make_item(src: dict, *, title: str, url: str, published=None, raw_summary: str = "",
              source_name: str | None = None, extra: dict | None = None, image: str = "") -> dict:
    """Normalize a fetched entry into the pre-enrichment Item shape."""
    pub = parse_date(published) or now_utc()
    return {
        "title": clean_text(title, 300),
        "url": url.strip(),
        "source_name": source_name or src["name"],
        "source_type": src["source_type"],
        "authority": src.get("authority", 1),
        "prefilter": bool(src.get("prefilter")),
        "published": iso(pub),
        "raw_summary": clean_text(raw_summary, src.get("_summary_chars", 500)),
        "extra": extra or {},
        "image": image,
    }
