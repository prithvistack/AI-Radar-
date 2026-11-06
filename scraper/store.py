"""Read/write site/data/items.json (the feed) and scraper/state.json (seen URLs, per-source memory)."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from fetchers.common import iso, now_utc, parse_date

ROOT = Path(__file__).resolve().parent.parent
ITEMS_PATH = ROOT / "site" / "data" / "items.json"
STATE_PATH = ROOT / "scraper" / "state.json"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_items() -> list[dict]:
    return _load(ITEMS_PATH, {}).get("items", [])


def load_state() -> dict:
    state = _load(STATE_PATH, {})
    state.setdefault("seen", {})
    return state


def save_items(items: list[dict], limits: dict) -> int:
    """Keep a rolling window, newest first. To keep the file small enough for a phone, low-importance
    stories (not in the top tier) are dropped once they're older than prune_after_hours, and the total is capped."""
    now = now_utc()
    cutoff = now - timedelta(days=limits.get("feed_days", 21))
    prune_before = now - timedelta(hours=limits.get("prune_after_hours", 48))
    min_sig = limits.get("prune_min_significance", 4)
    kept = []
    for it in items:
        published = parse_date(it["published"])
        if published < cutoff:
            continue
        if published < prune_before and it.get("significance", 0) < min_sig and it.get("tier") != "top":
            continue
        kept.append(it)
    kept.sort(key=lambda it: it["published"], reverse=True)
    kept = kept[: limits.get("max_items", 1500)]
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ITEMS_PATH.write_text(json.dumps({"updated": iso(now), "items": kept},
                                     ensure_ascii=False, separators=(",", ":")))
    return len(kept)


def save_state(state: dict, seen_ttl_days: int) -> None:
    cutoff = iso(now_utc() - timedelta(days=seen_ttl_days))
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
    STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True))
