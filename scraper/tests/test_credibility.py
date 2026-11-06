"""Credibility weights, independent-source counting and the top/more tier split."""
from datetime import timedelta
from pathlib import Path

import yaml

import credibility
from dedupe import cluster_items
from fetchers.common import iso, now_utc
from rank import rank

CFG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text())
LIMITS = CFG["limits"]


def item(title, url, source, source_type="news", authority=2, hours_ago=2):
    return {"title": title, "url": url, "source_name": source, "source_type": source_type,
            "authority": authority, "published": iso(now_utc() - timedelta(hours=hours_ago)),
            "raw_summary": "", "extra": {}}


def finish(clusters, **llm):
    """Run credibility + ranking with given (simulated) LLM judgments."""
    for c in clusters:
        c.update(llm)
    credibility.apply_all(clusters, CFG)
    rank(clusters, CFG["ranking"])
    return clusters


STRONG = dict(significance=8, substance=True, hype=False)
NVIDIA_TITLE = "NVIDIA releases Nemotron 5 open model family with 1M token context"


def test_nvidia_self_announcement_without_independent_coverage_lands_in_more():
    clusters, _ = cluster_items([item(NVIDIA_TITLE, "https://blogs.nvidia.com/blog/nemotron-5/", "NVIDIA",
                                      source_type="lab", authority=3)], [], LIMITS)
    c = finish(clusters, **STRONG)[0]
    assert c["first_party"] and c["independent_sources"] == 0
    assert c["credibility_weight"] == 0.7
    assert c["claim_status"] == "company-reported"
    assert c["tier"] == "more"


def test_same_story_reaches_top_after_reuters_covers_it():
    existing, _ = cluster_items([item(NVIDIA_TITLE, "https://blogs.nvidia.com/blog/nemotron-5/", "NVIDIA",
                                      source_type="lab", authority=3)], [], LIMITS)
    finish(existing, **STRONG)
    before = existing[0]["score"]
    reuters = item("Nvidia releases Nemotron 5 open model family with 1 million token context window",
                   "https://news.google.com/rss/articles/abc", "Reuters", authority=3, hours_ago=1)
    created, touched = cluster_items([reuters], existing, LIMITS)
    assert created == [] and len(touched) == 1
    c = finish(existing)[0]
    assert c["independent_sources"] == 1 and c["has_tier1_source"]
    assert c["credibility_weight"] == 1.0
    assert c["claim_status"] == "independently-verified"
    assert c["tier"] == "top"
    assert c["score"] > before


def test_opinion_listicle_never_reaches_top():
    # Even a tier-1 outlet, wide coverage and a (mistakenly) high significance can't lift a no-substance item.
    title = "5 AI stocks to buy now before the next big rally"
    clusters, _ = cluster_items([
        item(title, "https://reuters.com/a", "Reuters", authority=3),
        item(title, "https://techcrunch.com/a", "TechCrunch"),
        item(title, "https://theverge.com/a", "The Verge"),
    ], [], LIMITS)
    assert len(clusters) == 1
    c = finish(clusters, significance=10, substance=False, hype=False)[0]
    assert c["independent_sources"] == 3
    assert c["tier"] == "more"
    from enrich import heuristic  # the no-LLM fallback also marks it as lacking substance
    assert heuristic(c, CFG["ranking"]["fallback_significance"])["substance"] is False


def test_hype_and_low_significance_stay_out_of_top():
    clusters, _ = cluster_items([item("OpenAI ships GPT-6 with new reasoning mode for developers",
                                      "https://reuters.com/gpt6", "Reuters", authority=3)], [], LIMITS)
    assert finish(clusters, significance=9, substance=True, hype=True)[0]["tier"] == "more"
    assert finish(clusters, significance=6, substance=True, hype=False)[0]["tier"] == "more"
    assert finish(clusters, significance=7, substance=True, hype=False)[0]["tier"] == "top"


def test_same_owner_counts_as_first_party():
    clusters, _ = cluster_items([item("Reddit signs AI data licensing deal with Google for training data",
                                      "https://arstechnica.com/reddit", "Ars Technica")], [], LIMITS)
    c = finish(clusters, **STRONG)[0]
    assert c["first_party"] and c["independent_sources"] == 0 and c["tier"] == "more"


def test_lone_unknown_outlet_gets_tier3_weight_and_press_wires_are_first_party():
    clusters, _ = cluster_items([
        item("Startup unveils AI chip for edge inference workloads today", "https://smallblog.example/a", "Some Blog"),
        item("Acme launches AI platform for enterprise analytics teams", "https://prnewswire.com/a", "PR Newswire"),
    ], [], LIMITS)
    finish(clusters, **STRONG)
    by_src = {c["sources"][0]["name"]: c for c in clusters}
    assert by_src["Some Blog"]["credibility_weight"] == 0.45 and not by_src["Some Blog"]["first_party"]
    assert by_src["PR Newswire"]["first_party"] and by_src["PR Newswire"]["claim_status"] == "company-reported"


def test_repo_weight_depends_on_stars():
    low, _ = cluster_items([item("someone/tool: agent framework", "https://github.com/someone/tool", "GitHub",
                                 source_type="repo")], [], LIMITS)
    low[0]["extra"] = {"stars": 200}
    high, _ = cluster_items([item("big/tool: agent framework", "https://github.com/big/tool", "GitHub",
                                  source_type="repo")], [], LIMITS)
    high[0]["extra"] = {"stars": 5000}
    finish(low + high, **STRONG)
    assert low[0]["credibility_weight"] == 0.45 and high[0]["credibility_weight"] == 0.75
