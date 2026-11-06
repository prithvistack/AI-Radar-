"""LLM enrichment (Gemini, or Groq's OpenAI-compatible API) with a heuristic fallback when there is no key or the call fails."""
from __future__ import annotations

import json
import os
import re
import time

import requests
from rapidfuzz import fuzz

CATEGORIES = {"model-release", "research", "tool", "product", "business", "policy", "other"}

SYSTEM_PROMPT = """You are a skeptical senior editor for a personal AI news feed read by a computer engineering student who knows ML fundamentals (regression, neural nets, CNNs) but not every new term.
Be strict: most items are NOT significant. Companies promote themselves; news sites rehash press releases.
Be factual; use only the given text. Do not invent numbers or claims.

Each input item has "first_party": true when the text comes from the company itself (its blog, release notes,
model card or a press release). For first-party items, attribute performance or quality claims in the summary
("NVIDIA says its model beats X"); never state them as fact.

Return strict JSON: an object {"items": [...]} with one entry per input item:
{"id": "<same id as input>",
 "summary": "<=30 words, plain English, what happened and why it matters",
 "category": "model-release|research|tool|product|business|policy|other",
 "significance": 1-10,
 "substance": true/false,
 "hype": true/false,
 "key_terms": ["up to 3 technical terms a student might not know"]}

significance guide. Score the event itself, not how well or how breathlessly it is written; a press-release
tone doesn't make a real release less important. Most items are 1-6, but real events below do get these scores:
- 9-10: a new frontier model from a major lab (a new GPT, Claude, Gemini, Llama or similar, including specialised
  variants), a major breakthrough, industry-shifting news
- 7-8: notable open-weight model release, strong paper with results, big product launch with real access,
  binding regulation, major security finding
- 4-6: useful tool, partnership, funding, or solid incremental news
- 1-3: minor, opinion, marketing, rehash, stock commentary

substance: true only if the item has concrete new information: a release with access or weights, a paper
with results, real numbers, or actual policy/legal text. false for listicles, opinion, rehashes, stock tips,
event promos and marketing.

hype: true for vague claims, superlatives without data, or clickbait framing.

Some items have an empty raw_summary because the publisher blocks access. Missing text is not evidence of
hype or lack of substance: judge those from what the headline reports. A headline stating a concrete event
("X launches Y", "court rules Z") has substance:true and hype:false unless the headline itself is clickbait."""


class LLMError(Exception):
    pass


def _parse(content: str) -> list[dict]:
    data = json.loads(content)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("response has no items list")
    return items


def _call_gemini(llm: dict, model: str, payload_items: list[dict]) -> list[dict]:
    # Gemini answers 503 during demand spikes; those clear quickly, so wait rather than use up the fallback.
    for wait in (*llm.get("overload_waits", [15, 30]), None):
        resp = requests.post(
            llm["gemini_base_url"].format(model=model),
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": json.dumps(payload_items, ensure_ascii=False)}]}],
                "generationConfig": {"temperature": llm.get("temperature", 0.2), "responseMimeType": "application/json"},
            },
            timeout=llm.get("timeout_seconds", 60),
        )
        if resp.status_code != 503 or wait is None:
            break
        print(f"  {model} overloaded; retrying in {wait}s")
        time.sleep(wait)
    if resp.status_code == 429:
        raise LLMError("rate limited")
    _raise_with_detail(resp)
    parts = resp.json()["candidates"][0]["content"]["parts"]
    return _parse("".join(p.get("text", "") for p in parts))


def _call_groq(llm: dict, model: str, payload_items: list[dict]) -> list[dict]:
    resp = requests.post(
        llm["base_url"],
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": llm.get("temperature", 0.2),
            "response_format": {"type": "json_object"},
            **llm.get("model_params", {}).get(model, {}),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload_items, ensure_ascii=False)},
            ],
        },
        timeout=llm.get("timeout_seconds", 60),
    )
    if resp.status_code == 429:
        raise LLMError("rate limited")
    _raise_with_detail(resp)
    return _parse(resp.json()["choices"][0]["message"]["content"])


def _raise_with_detail(resp: requests.Response) -> None:
    if resp.status_code >= 400:
        raise ValueError(f"HTTP {resp.status_code}: {resp.text[:200]}")


def _key_name(model: str) -> str:
    return "GEMINI_API_KEY" if model.startswith("gemini") else "GROQ_API_KEY"


def _call(llm: dict, model: str, payload_items: list[dict]) -> list[dict]:
    fn = _call_gemini if model.startswith("gemini") else _call_groq
    return fn(llm, model, payload_items)


def same_text(a: str, b: str) -> bool:
    """True when a "summary" only repeats the headline."""
    na, nb = (re.sub(r"\W+", " ", s or "").strip().lower() for s in (a, b))
    return not na or not nb or na.startswith(nb) or nb.startswith(na) or fuzz.ratio(na, nb) >= 85


def _validate(entry: dict) -> dict | None:
    try:
        significance = int(round(float(entry["significance"])))
    except (KeyError, TypeError, ValueError):
        return None
    summary = str(entry.get("summary", "")).strip()
    if not summary or "substance" not in entry:
        return None
    category = str(entry.get("category", "other")).strip().lower()
    terms = entry.get("key_terms") or []
    return {
        "summary": _truncate_words(summary, 35),
        "category": category if category in CATEGORIES else "other",
        "significance": max(1, min(10, significance)),
        "substance": _as_bool(entry.get("substance")),
        "hype": _as_bool(entry.get("hype", False)),
        "key_terms": [str(t).strip() for t in terms if str(t).strip()][:3] if isinstance(terms, list) else [],
    }


def _as_bool(v) -> bool:
    return v.strip().lower() == "true" if isinstance(v, str) else bool(v)


def _truncate_words(text: str, n: int) -> str:
    words = text.split()
    return text if len(words) <= n else " ".join(words[:n]) + "…"


_POLICY = re.compile(r"\b(policy|regulat\w*|law|lawsuit|sue[sd]?|court|senate|congress|ban|copyright|EU AI Act|government|minister)\b", re.I)
_BUSINESS = re.compile(r"\b(funding|raises?|raised|acquir\w*|valuation|IPO|revenue|deal|invest\w*|layoffs?|stock|shares)\b", re.I)
_MODEL = re.compile(r"\b(model|weights|checkpoint|LLM)\b", re.I)
_LAUNCH = re.compile(r"\b(launch\w*|releas\w*|introduc\w*|unveil\w*|announc\w*|open[- ]sourc\w*)\b", re.I)
# Listicles, opinion, stock tips and how-tos: no concrete new information.
_NO_SUBSTANCE = re.compile(
    r"(^\d+\s+(ways|things|reasons|tips|stocks?|tools|ai tools|best)\b|\btop \d+\b|\bbest .+ (for|in) 20\d\d\b|"
    r"\b(opinion|op-ed|column|editorial|podcast|webinar|how to|explained|what is|why you should|should you|"
    r"stocks? to buy|buy now|stock splits?|price prediction|is it too late)\b|\?$)", re.I)
_HYPE = re.compile(r"\b(revolutionar\w*|game[- ]chang\w*|mind[- ]blowing|insane|jaw[- ]dropping|shocking|"
                   r"you won.t believe|changes everything|blows .+ away|destroys|killer|unbelievable|"
                   r"the wait is over)\b", re.I)


def heuristic(item: dict, fallback_significance: dict) -> dict:
    text = f"{item['title']} {item.get('raw_summary', '')}"
    st = item["source_type"]
    if st == "paper":
        category = "research"
    elif st == "model":
        category = "model-release"
    elif st in ("repo", "release"):
        category = "tool"
    elif _POLICY.search(text):
        category = "policy"
    elif _BUSINESS.search(text):
        category = "business"
    elif _MODEL.search(text) and _LAUNCH.search(text):
        category = "model-release"
    elif st == "lab" or _LAUNCH.search(text):
        category = "product"
    else:
        category = "other"
    return {
        # Empty rather than the headline again; cards hide an empty summary.
        "summary": _truncate_words(item.get("raw_summary") or "", 30),
        "category": category,
        "significance": fallback_significance.get(st, 4),
        "substance": st in ("paper", "release", "model") or not _NO_SUBSTANCE.search(item["title"]),
        "hype": bool(_HYPE.search(item["title"])),
        "key_terms": [],
    }


def enrich(clusters: list[dict], cfg: dict) -> dict:
    """Enrich clusters in place. Unenriched items always get heuristic values first, so the
    feed is complete even with no key; LLM results overwrite them for as many items as the call cap allows."""
    llm, ranking = cfg["llm"], cfg["ranking"]
    stats = {"llm_calls": 0, "llm_items": 0, "fallback_items": 0}
    pending = [c for c in clusters if not c.get("enriched")]
    for c in clusters:
        # Items written before the credibility update carry "importance"; migrate them.
        if "importance" in c:
            imp = c.pop("importance")
            if c.get("enriched"):
                c.setdefault("significance", imp)
                c.setdefault("substance", True)
    for c in pending:
        if not c.get("significance"):  # new clusters start at 0
            c.update(heuristic(c, ranking["fallback_significance"]))
    models = [m for m in (llm["model"], llm.get("fallback_model")) if m and os.environ.get(_key_name(m), "").strip()]
    if not models or not pending:
        stats["fallback_items"] = len(pending)
        return stats

    # Newest first, so a backlog (e.g. after running without a key) clears from the top of the feed.
    pending.sort(key=lambda c: c["published"], reverse=True)
    size = llm.get("batch_size", 15)
    for start in range(0, len(pending), size):
        if stats["llm_calls"] >= llm.get("max_calls_per_run", 5):
            break
        batch = pending[start:start + size]
        payload = [{"id": c["id"], "title": c["title"], "source": c["sources"][0]["name"],
                    "first_party": bool(c.get("first_party")),
                    "raw_summary": (c.get("raw_summary") or "")[:600]} for c in batch]
        results = None
        for attempt in range(2):  # retry once on parse/validation failure
            model = models[min(attempt, len(models) - 1)]  # the retry goes to the fallback, if there is one
            if stats["llm_calls"]:
                groq = _key_name(model) == "GROQ_API_KEY"
                time.sleep(llm.get("groq_min_seconds_between_calls" if groq else "min_seconds_between_calls", 0))
            stats["llm_calls"] += 1
            try:
                results = _call(llm, model, payload)
                break
            except LLMError:
                if len(models) > 1:
                    models.pop(0)
                    print(f"  LLM rate limited; switching to {models[0]}")
                    continue
                print("  LLM rate limited; stopping enrichment for this run")
                return stats
            except (ValueError, KeyError, json.JSONDecodeError, requests.RequestException) as exc:
                print(f"  LLM batch failed (attempt {attempt + 1}): {exc}")
        if results is None:
            continue
        by_id = {str(r.get("id")): r for r in results if isinstance(r, dict)}
        for c in batch:
            valid = _validate(by_id.get(c["id"], {}))
            if valid:
                # With only a headline to go on (e.g. Reuters), a "summary" would just reword it.
                if not c.get("raw_summary") or same_text(valid["summary"], c["title"]):
                    valid["summary"] = ""
                c.update(valid)
                c["enriched"] = True
                stats["llm_items"] += 1
    stats["fallback_items"] = sum(1 for c in pending if not c.get("enriched"))
    return stats
