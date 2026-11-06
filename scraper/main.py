"""One AI Radar run: fetch -> skip seen -> prefilter -> dedupe -> enrich -> rank -> store.

Usage (from the repo root):
  python scraper/main.py                 # full run, writes site/data/items.json + scraper/state.json
  python scraper/main.py --print         # also print the new items
  python scraper/main.py --dry-run       # fetch and print, write nothing
  python scraper/main.py --sources "TechCrunch,Hacker News"
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import credibility  # noqa: E402
import dedupe  # noqa: E402
import enrich  # noqa: E402
import filter as relevance  # noqa: E402
import images  # noqa: E402
import rank  # noqa: E402
import resolve  # noqa: E402
import store  # noqa: E402
from fetchers import FETCHERS, common  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_env() -> None:
    """Local runs read keys from the repo's .env; in GitHub Actions they come from secrets."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#") and value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def fetch_one(src: dict, ctx: dict) -> tuple[str, list[dict], str | None, float]:
    start = time.monotonic()
    try:
        return src["name"], FETCHERS[src["type"]](src, ctx), None, time.monotonic() - start
    except Exception as exc:  # one failing source must never break the run
        return src["name"], [], f"{type(exc).__name__}: {exc}"[:200], time.monotonic() - start


def fetch_all(cfg: dict, state: dict, only: set[str] | None) -> list[dict]:
    sources = [s for s in cfg["sources"] if not only or s["name"] in only]
    for s in sources:
        s["_summary_chars"] = cfg["limits"].get("raw_summary_chars", 500)
    ctx = {"state": state, "cfg": cfg}
    items = []
    with ThreadPoolExecutor(max_workers=cfg["http"].get("max_workers", 12)) as pool:
        for src, (name, got, err, secs) in zip(sources, pool.map(lambda s: fetch_one(s, ctx), sources)):
            if err:
                level = "skip" if src.get("optional") else "FAIL"
                print(f"  {level:4} {name:24} {secs:5.1f}s  {err}")
            else:
                print(f"  ok   {name:24} {secs:5.1f}s  {len(got)} items")
            items.extend(got)
    return items


def print_items(items: list[dict]) -> None:
    for it in items:
        srcs = it.get("sources") or [{"name": it.get("source_name")}]
        more = f" +{len(srcs) - 1}" if len(srcs) > 1 else ""
        line = f"[{it['source_type']:9}] {it['published'][:16]}  {srcs[0]['name']}{more}: {it['title'][:100]}"
        if "score" in it:
            line = f"{it['score']:5.1f} " + line
        print(line)
        if it.get("summary"):
            print(f"        {it['summary'][:160]}")


def run(args) -> int:
    load_env()
    cfg = load_config()
    common.configure(cfg["http"])
    limits = cfg["limits"]
    state = store.load_state()
    existing = store.load_items()
    only = {s.strip() for s in args.sources.split(",")} if args.sources else None

    print("Fetching…")
    fetched = fetch_all(cfg, state, only)

    # Drop stale entries (feeds often carry weeks of history) and anything seen in an earlier run.
    cutoff = common.iso(common.now_utc() - timedelta(days=limits.get("max_item_age_days", 4)))
    fresh = [it for it in fetched if it["published"] >= cutoff and it["title"]]
    unseen = [it for it in fresh if dedupe.url_id(it["url"]) not in state["seen"]]
    relevant = relevance.prefilter(unseen, cfg["keywords"])
    print(f"Fetched {len(fetched)}, fresh {len(fresh)}, unseen {len(unseen)}, relevant {len(relevant)}")

    # Keys are taken first: feeds keep listing the Google link, and that's what must be recognised next run.
    seen_keys = [dedupe.url_id(it["url"]) for it in fresh]
    print(f"Resolved {resolve.resolve_new(relevant, cfg)} new links; repaired {resolve.backfill(existing, cfg)} stored stories")

    new_clusters, touched = dedupe.cluster_items(relevant, existing, limits)
    print(f"New stories {len(new_clusters)}, existing stories gaining sources {len(touched)}")

    if args.rescore:
        for it in existing:
            it["enriched"] = False
    if args.max_llm_calls is not None:
        cfg["llm"]["max_calls_per_run"] = args.max_llm_calls
    if args.no_llm:
        os.environ.pop("GROQ_API_KEY", None)
    all_items = existing + new_clusters
    credibility.apply_all(all_items, cfg)  # before enrichment: the LLM is told which items are first-party
    stats = enrich.enrich(all_items, cfg)
    print(f"Enrichment: {stats}")
    rank.rank(all_items, cfg["ranking"])
    if not args.dry_run:
        print(f"Found {images.backfill(all_items, cfg)} cover images")

    if args.print or args.dry_run:
        print("\nNew stories:")
        print_items(sorted(new_clusters, key=lambda c: -c["score"]))
    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0

    # Only fresh items need remembering; older ones are dropped by the age filter anyway.
    # First-seen time is kept, so entries expire after seen_ttl_days.
    now = common.iso(common.now_utc())
    for key in seen_keys:
        state["seen"].setdefault(key, now)
    kept = store.save_items(all_items, limits)
    store.save_state(state, limits.get("seen_ttl_days", 30))
    print(f"Wrote {kept} items to {store.ITEMS_PATH.relative_to(store.ROOT)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--print", action="store_true", help="print new stories")
    p.add_argument("--dry-run", action="store_true", help="fetch and print without writing files")
    p.add_argument("--no-llm", action="store_true", help="skip the LLM even if GROQ_API_KEY is set")
    p.add_argument("--rescore", action="store_true", help="re-run the LLM on stored stories, not just new ones")
    p.add_argument("--max-llm-calls", type=int, help="override llm.max_calls_per_run (e.g. to clear a backlog)")
    p.add_argument("--sources", help="comma-separated source names to run (default: all)")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
