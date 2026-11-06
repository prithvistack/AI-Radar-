"""Score and tier every story.

score = significance × credibility_weight × (1 + k·log2(1 + independent_sources))
        × (hype_factor if hype) × (no_substance_factor if not substance) × 0.5^(age_hours / half_life)

tier "top" needs significance ≥ min, substance, no hype, and a tier-1 source or enough independent coverage.
"""
from __future__ import annotations

import math

from fetchers.common import now_utc, parse_date


def score(item: dict, ranking: dict, now=None) -> float:
    now = now or now_utc()
    age_hours = max(0.0, (now - parse_date(item["published"])).total_seconds() / 3600)
    s = (item.get("significance", 0)
         * item.get("credibility_weight", 0.45)
         * (1 + ranking.get("coverage_factor", 0.3) * math.log2(1 + item.get("independent_sources", 0))))
    if item.get("hype"):
        s *= ranking.get("hype_factor", 0.5)
    if not item.get("substance", False):
        s *= ranking.get("no_substance_factor", 0.4)
    s *= 0.5 ** (age_hours / ranking.get("half_life_hours", 36))
    return round(s, 3)


def tier(item: dict, ranking: dict) -> str:
    top = ranking.get("top", {})
    ok = (item.get("significance", 0) >= top.get("min_significance", 7)
          and (item.get("substance", False) or not top.get("require_substance", True))
          and not (item.get("hype") and top.get("exclude_hype", True))
          and ((top.get("tier1_qualifies", True) and item.get("has_tier1_source", False))
               or item.get("independent_sources", 0) >= top.get("min_independent_sources", 1)))
    return "top" if ok else "more"


def rank(items: list[dict], ranking: dict) -> None:
    now = now_utc()
    for it in items:
        it["score"] = score(it, ranking, now)
        it["tier"] = tier(it, ranking)
