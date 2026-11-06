"""Hugging Face Daily Papers (community-upvoted arXiv) and trending models."""
from __future__ import annotations

from datetime import timedelta

from .common import get, make_item, now_utc, parse_date


def fetch_papers(src: dict, ctx: dict) -> list[dict]:
    items, seen = [], set()
    today = now_utc().date()
    for offset in range(src.get("days_back", 1)):
        day = (today - timedelta(days=offset)).isoformat()
        # The API serves the latest list without a date; with one it serves that day (may be empty early on).
        url = "https://huggingface.co/api/daily_papers" + ("" if offset == 0 else f"?date={day}")
        for entry in get(url).json():
            paper = entry.get("paper", {})
            arxiv_id = paper.get("id")
            upvotes = int(paper.get("upvotes") or 0)
            if not arxiv_id or arxiv_id in seen or upvotes < src.get("min_upvotes", 0):
                continue
            seen.add(arxiv_id)
            items.append(make_item(
                src,
                title=entry.get("title") or paper.get("title", ""),
                url=f"https://arxiv.org/abs/{arxiv_id}",
                published=paper.get("submittedOnDailyAt") or entry.get("publishedAt"),
                raw_summary=paper.get("ai_summary") or paper.get("summary", ""),
                extra={
                    "arxiv_id": arxiv_id,
                    "pdf": f"https://arxiv.org/pdf/{arxiv_id}",
                    "hf_url": f"https://huggingface.co/papers/{arxiv_id}",
                    "upvotes": upvotes,
                },
            ))
    return items


def fetch_models(src: dict, ctx: dict) -> list[dict]:
    """Recently created models in the trending list. Already-seen URLs are dropped upstream,
    so a model shows up once: when it first enters the list."""
    cutoff = now_utc() - timedelta(days=src.get("max_model_age_days", 14))
    items = []
    for m in get(src["url"]).json()[: src.get("top_n", 30)]:
        created = parse_date(m.get("createdAt"))
        if not created or created < cutoff:
            continue
        model_id = m["id"]
        task = m.get("pipeline_tag") or "model"
        items.append(make_item(
            src,
            title=f"{model_id} is trending on Hugging Face",
            url=f"https://huggingface.co/{model_id}",
            published=created,
            raw_summary=f"New {task} model {model_id}: {m.get('likes', 0)} likes, "
                        f"{m.get('downloads', 0)} downloads. Tags: {', '.join(m.get('tags', [])[:8])}.",
            extra={"likes": m.get("likes", 0), "downloads": m.get("downloads", 0), "task": task},
        ))
    return items
