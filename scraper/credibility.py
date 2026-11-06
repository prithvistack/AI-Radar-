"""Source credibility: tier weights, first-party detection, same-owner rule, independent-source counts.

Everything is derived from source names + config on every run, so editing config.yaml re-scores old items too.
"""
from __future__ import annotations

import re

FIRST_PARTY_TYPES = {"lab", "release", "model"}


class Credibility:
    def __init__(self, cfg: dict):
        cred = cfg["credibility"]
        self.first_party_weight = cred.get("first_party_weight", 0.7)
        self.default_weight = cred.get("default_weight", 0.45)
        self.repo_stars_for_tier2 = cred.get("repo_stars_for_tier2", 1000)
        self.tier_of: dict[str, int] = {}
        self.tier_weight: dict[int, float] = {}
        for tier, spec in cred.get("tiers", {}).items():
            self.tier_weight[int(tier)] = float(spec["weight"])
            for name in spec.get("sources", []):
                self.tier_of[name.lower()] = int(tier)
        # Feed sources configured as first-party (lab blogs, releases, HF model cards), plus extra publisher
        # names that show up through Google News (company newsrooms, press-release wires).
        self.first_party_names = {n.lower() for n in cred.get("first_party_publishers", [])}
        for src in cfg.get("sources", []):
            fp = src.get("first_party", src.get("source_type") in FIRST_PARTY_TYPES)
            if fp:
                self.first_party_names.add(src["name"].lower())
        self.owner_groups = []
        for group in cred.get("same_owner", []):
            outlets = {o.lower() for o in group["outlets"]}
            pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(c) for c in group["companies"]) + r")(?!\w)", re.I)
            self.owner_groups.append((outlets, pattern))

    def is_first_party(self, source: dict) -> bool:
        return source["name"].lower() in self.first_party_names

    def is_same_owner(self, source: dict, cluster: dict) -> bool:
        """The outlet is covering its own company, parent, subsidiary or major investor."""
        name = source["name"].lower()
        text = f"{cluster['title']} {source.get('title', '')}"
        return any(name in outlets and pattern.search(text) for outlets, pattern in self.owner_groups)

    def tier(self, source: dict) -> int | None:
        return self.tier_of.get(source["name"].lower())

    def weight(self, source: dict, cluster: dict) -> float:
        tier = self.tier(source)
        if tier is not None:
            return self.tier_weight[tier]
        if cluster["source_type"] == "repo" and source["name"] == "GitHub":
            stars = cluster.get("extra", {}).get("stars", 0)
            return self.tier_weight.get(2, 0.75) if stars >= self.repo_stars_for_tier2 else self.default_weight
        return self.default_weight

    def apply(self, cluster: dict) -> None:
        independent: dict[str, float] = {}
        first_party = False
        has_tier1 = False
        for s in cluster["sources"]:
            if self.is_first_party(s) or self.is_same_owner(s, cluster):
                first_party = True
                continue
            independent[s["name"]] = self.weight(s, cluster)
            has_tier1 = has_tier1 or self.tier(s) == 1

        cluster["first_party"] = first_party
        cluster["independent_sources"] = len(independent)
        cluster["has_tier1_source"] = has_tier1
        if independent:
            # A first-party story rises to the best independent outlet's weight once one covers it.
            cluster["credibility_weight"] = max(independent.values())
        else:
            cluster["credibility_weight"] = self.first_party_weight if first_party else self.default_weight
        if not first_party:
            cluster["claim_status"] = "n/a"
        elif independent:
            cluster["claim_status"] = "independently-verified"
        else:
            cluster["claim_status"] = "company-reported"


def apply_all(items: list[dict], cfg: dict) -> None:
    cred = Credibility(cfg)
    for it in items:
        cred.apply(it)
