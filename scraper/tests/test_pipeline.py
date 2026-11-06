import json
from datetime import timedelta

import enrich
import images
from bs4 import BeautifulSoup
from fetchers.common import cover_url, iso, now_utc, parse_date
from fetchers.github import is_major_or_minor
from fetchers.rss import entry_image
from filter import build_matcher, is_relevant
from rank import score

RANKING = {"coverage_factor": 0.3, "hype_factor": 0.5, "no_substance_factor": 0.4, "half_life_hours": 36}
CFG = {
    "llm": {"base_url": "https://example.invalid", "model": "m", "batch_size": 15, "max_calls_per_run": 2},
    "ranking": {**RANKING, "fallback_significance": {"news": 4, "paper": 5, "lab": 5}},
}


def cluster(**kw):
    base = {"id": "x", "title": "OpenAI raises $40B funding round", "url": "https://a.com", "sources": [{"name": "A"}],
            "source_type": "news", "published": iso(now_utc()), "raw_summary": "OpenAI raised money.",
            "summary": "", "enriched": False}
    return {**base, **kw}


def test_keyword_prefilter_uses_word_boundaries():
    m = build_matcher(["AI", "LLM", "machine learning"])
    assert is_relevant({"title": "New AI chip announced"}, m)
    assert is_relevant({"title": "x", "raw_summary": "uses an LLM."}, m)
    assert not is_relevant({"title": "Said the captain of the trail"}, m)  # "ai" inside words
    assert not is_relevant({"title": "Football transfer news"}, m)


def test_semver_major_minor_only():
    assert is_major_or_minor("v4.57.0") and is_major_or_minor("0.12") and is_major_or_minor("v2.9")
    assert not is_major_or_minor("v4.57.1") and not is_major_or_minor("b6543") and not is_major_or_minor("v1.0.0rc1")


def test_parse_date_formats():
    assert iso(parse_date("Thu, 05 Nov 2026 15:00:45 +0000")) == "2026-11-05T15:00:45Z"
    assert iso(parse_date("2026-10-13")) == "2026-10-13T00:00:00Z"
    assert iso(parse_date("2026-10-12T00:00:00.000Z")) == "2026-10-12T00:00:00Z"
    assert parse_date("not a date") is None


def test_score_formula():
    now = now_utc()
    base = dict(significance=8, credibility_weight=1.0, independent_sources=3, substance=True, hype=False,
                published=iso(now))
    assert score(cluster(**base), RANKING, now) == 8 * 1.0 * (1 + 0.3 * 2)
    assert score(cluster(**{**base, "hype": True}), RANKING, now) == round(8 * 1.6 * 0.5, 3)
    assert score(cluster(**{**base, "substance": False}), RANKING, now) == round(8 * 1.6 * 0.4, 3)
    day_and_half = cluster(**{**base, "published": iso(now - timedelta(hours=36))})
    assert score(day_and_half, RANKING, now) == round(8 * 1.6 * 0.5, 3)
    no_lab_bonus = cluster(**{**base, "source_type": "lab"})
    assert score(no_lab_bonus, RANKING, now) == score(cluster(**base), RANKING, now)


def test_no_api_key_falls_back_to_heuristics(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    items = [cluster(), cluster(id="p", source_type="paper", title="A study of attention")]
    stats = enrich.enrich(items, CFG)
    assert stats["llm_calls"] == 0 and stats["fallback_items"] == 2
    assert items[0]["category"] == "business" and items[0]["significance"] == 4 and items[0]["substance"]
    assert items[1]["category"] == "research" and items[1]["summary"]
    assert not items[0]["enriched"]


class FakeResp:
    def __init__(self, content, status=200):
        self.status_code, self._content = status, content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_llm_results_are_validated_and_bad_json_retried(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    good = json.dumps({"items": [{"id": "x", "summary": "OpenAI raised a lot.", "category": "Business",
                                  "significance": 14, "substance": "false", "hype": False,
                                  "key_terms": ["a", "b", "c", "d"]}]})
    responses = iter([FakeResp("not json"), FakeResp(good)])
    monkeypatch.setattr(enrich.requests, "post", lambda *a, **k: next(responses))
    items = [cluster()]
    stats = enrich.enrich(items, CFG)
    assert stats == {"llm_calls": 2, "llm_items": 1, "fallback_items": 0}
    it = items[0]
    assert it["enriched"] and it["category"] == "business" and it["significance"] == 10 and len(it["key_terms"]) == 3
    assert it["substance"] is False


def test_llm_failure_keeps_heuristic(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(enrich.requests, "post", lambda *a, **k: FakeResp("[]"))
    items = [cluster()]
    stats = enrich.enrich(items, CFG)
    assert stats["fallback_items"] == 1 and items[0]["summary"] and not items[0]["enriched"]


def test_first_party_flag_is_sent_to_llm(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    sent = {}

    def fake_post(*a, **k):
        sent.update(json.loads(k["json"]["messages"][1]["content"])[0])
        return FakeResp("[]")
    monkeypatch.setattr(enrich.requests, "post", fake_post)
    enrich.enrich([cluster(first_party=True)], CFG)
    assert sent["first_party"] is True
    assert "skeptical senior editor" in enrich.SYSTEM_PROMPT and "NVIDIA says" in enrich.SYSTEM_PROMPT


def test_old_importance_field_is_migrated(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    old = cluster(importance=6)
    enrich.enrich([old], CFG)
    assert "importance" not in old and old["significance"] == 4


def test_heuristic_flags_listicles_and_hype():
    fb = {"news": 4}
    assert not enrich.heuristic(cluster(title="5 AI stocks to buy now before it's too late"), fb)["substance"]
    assert not enrich.heuristic(cluster(title="Opinion: AI agents are overrated"), fb)["substance"]
    assert enrich.heuristic(cluster(title="The Wait Is Over: 2 Historic AI Stock Splits"), fb)["hype"]
    assert enrich.heuristic(cluster(title="Dropbox acquires Tigris Data for $350M"), fb)["substance"]


def test_new_cluster_gets_heuristic_values(monkeypatch):
    from dedupe import new_cluster
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    c = new_cluster({"title": "Startup releases open model for code", "url": "https://x.com/a", "source_name": "X",
                     "source_type": "news", "authority": 1, "published": iso(now_utc()), "raw_summary": "", "extra": {}})
    enrich.enrich([c], CFG)
    assert c["significance"] == 4 and c["substance"] is True


def test_summary_that_repeats_the_headline_is_dropped(monkeypatch):
    import resolve
    title = "Box expands Nvidia partnership, unveils AI workflow builder"
    assert resolve.same_text(title, title) and resolve.same_text("", title)
    assert resolve.same_text(title + " - Reuters", title)
    assert not resolve.same_text("Box said customers can now build agents on Nemotron models.", title)
    assert enrich.heuristic(cluster(title=title, raw_summary=""), {"news": 4})["summary"] == ""


def test_backfill_replaces_google_link_keeps_id_and_stops_retrying(monkeypatch):
    import resolve
    cfg = {"resolve": {"max_per_run": 10, "workers": 1}}
    google = "https://news.google.com/rss/articles/CBMiabc?oc=5"
    c = cluster(id="keep", url=google, title="Tencent launches open model", raw_summary="",
                sources=[{"name": "FF News", "url": google}])
    monkeypatch.setattr(resolve, "decode_google_url", lambda u: "https://ffnews.com/tencent")
    monkeypatch.setattr(resolve, "page_summary", lambda u, t: "Tencent released weights for its HY4 model under an open licence.")
    assert resolve.backfill([c], cfg) == 1
    assert c["url"] == "https://ffnews.com/tencent" and c["sources"][0]["url"] == c["url"] and c["id"] == "keep"
    assert c["summary"].startswith("Tencent released")

    blocked = cluster(url="https://reuters.com/a", title="Blocked story", raw_summary="",
                      sources=[{"name": "Reuters", "url": "https://reuters.com/a"}])
    monkeypatch.setattr(resolve, "page_summary", lambda u, t: "")
    for _ in range(4):
        resolve.backfill([blocked], cfg)
    assert blocked["resolve_tries"] == resolve.MAX_TRIES and blocked["summary"] == ""


def test_failed_google_decode_keeps_the_link(monkeypatch):
    import resolve
    monkeypatch.setattr(resolve, "decode_google_url", lambda u: None)
    google = "https://news.google.com/rss/articles/CBMiabc"
    assert resolve._fix(google, "A title here", "") == (google, "")


def test_headline_only_story_gets_scored_but_no_reworded_summary(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    good = json.dumps({"items": [{"id": "x", "summary": "OpenAI raised money.", "category": "business",
                                  "significance": 6, "substance": True, "hype": False}]})
    monkeypatch.setattr(enrich.requests, "post", lambda *a, **k: FakeResp(good))
    items = [cluster(raw_summary="")]
    enrich.enrich(items, CFG)
    assert items[0]["enriched"] and items[0]["significance"] == 6 and items[0]["summary"] == ""


def test_model_params_are_sent(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    sent = {}

    def fake_post(*a, **k):
        sent.update(k["json"])
        return FakeResp("[]")
    monkeypatch.setattr(enrich.requests, "post", fake_post)
    cfg = {**CFG, "llm": {**CFG["llm"], "model_params": {"m": {"reasoning_effort": "low"}}}}
    enrich.enrich([cluster()], cfg)
    assert sent["reasoning_effort"] == "low"


def test_gemini_model_uses_gemini_key_and_format(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    text = json.dumps({"items": [{"id": "x", "summary": "OpenAI raised a lot of money.", "category": "business",
                                  "significance": 7, "substance": True, "hype": False}]})
    sent = {}

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": _text}]}}]}
    _text = text

    def fake_post(url, **k):
        sent.update(url=url, headers=k["headers"])
        return Resp()
    monkeypatch.setattr(enrich.requests, "post", fake_post)
    cfg = {**CFG, "llm": {**CFG["llm"], "model": "gemini-x", "gemini_base_url": "https://g/{model}:gen"}}
    items = [cluster()]
    enrich.enrich(items, cfg)
    assert items[0]["enriched"] and items[0]["significance"] == 7
    assert sent["url"] == "https://g/gemini-x:gen" and sent["headers"]["x-goog-api-key"] == "g"


def test_cover_urls_reject_logos_and_force_https():
    assert cover_url("http://cdn.site.com/a/photo.jpg?x=1&amp;y=2") == "https://cdn.site.com/a/photo.jpg?x=1&y=2"
    for bad in ("", None, "/relative.jpg", "https://x.com/logo.png", "https://x.com/default-share.jpg",
                "https://x.com/a.svg", "https://opengraph.githubassets.com/1/o/r"):
        assert cover_url(bad) == ""


def test_feed_entry_image_sources():
    assert entry_image({"media_thumbnail": [{"url": "https://a.com/t.jpg"}]}) == "https://a.com/t.jpg"
    assert entry_image({"summary": '<p><img class="x" src="https://a.com/i.png"></p>'}) == "https://a.com/i.png"
    assert entry_image({"media_content": [{"url": "https://a.com/v.mp4", "medium": "video"}]}) == ""


def test_og_image_is_resolved_against_the_page():
    soup = BeautifulSoup('<meta property="og:image" content="/img/cover.jpg">', "html.parser")
    assert images.from_soup(soup, "https://a.com/post/1") == "https://a.com/img/cover.jpg"


def test_site_default_image_is_dropped():
    stories = [{"image": "https://a.com/same.jpg"} for _ in range(3)] + [{"image": "https://a.com/real.jpg"}]
    images.drop_generic(stories)
    assert [s["image"] for s in stories] == ["", "", "", "https://a.com/real.jpg"]
    assert stories[0]["image_tries"] == images.MAX_TRIES


def test_arxiv_figure_path_is_not_doubled(monkeypatch):
    page = '<figure><img src="2611.0001v1/fig.png"></figure>'
    resp = type("R", (), {"text": page, "url": "https://arxiv.org/html/2611.0001"})
    monkeypatch.setattr(images.common, "get", lambda *a, **k: resp)
    assert images.from_arxiv("2611.0001") == "https://arxiv.org/html/2611.0001v1/fig.png"
