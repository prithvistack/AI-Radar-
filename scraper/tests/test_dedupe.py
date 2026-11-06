from dedupe import canonicalize_url, cluster_items, titles_match, url_id

LIMITS = {"title_similarity": 85, "min_title_tokens": 4, "cluster_window_hours": 48}


def item(title, url, source="TechCrunch", authority=2, published="2026-11-05T10:00:00Z", source_type="news"):
    return {"title": title, "url": url, "source_name": source, "source_type": source_type,
            "authority": authority, "published": published, "raw_summary": "", "extra": {}}


class TestCanonicalize:
    def test_strips_tracking_params_and_www(self):
        assert canonicalize_url("https://www.example.com/a/b/?utm_source=x&utm_medium=rss&id=3&fbclid=abc#top") \
            == "https://example.com/a/b?id=3"

    def test_sorts_query_and_normalizes_scheme_host(self):
        assert canonicalize_url("http://Example.COM/x?b=2&a=1") == "https://example.com/x?a=1&b=2"

    def test_trailing_slash_and_double_slashes(self):
        assert canonicalize_url("https://example.com//news//post/") == "https://example.com/news/post"
        assert canonicalize_url("https://example.com/") == "https://example.com/"

    def test_arxiv_variants_collapse(self):
        expected = "https://arxiv.org/abs/2611.03517"
        for u in ("https://arxiv.org/abs/2611.03517v2", "https://arxiv.org/pdf/2611.03517",
                  "https://arxiv.org/pdf/2611.03517v1.pdf", "http://www.arxiv.org/html/2611.03517v3/"):
            assert canonicalize_url(u) == expected

    def test_google_news_opaque_link_kept_without_oc_param(self):
        u = "https://news.google.com/rss/articles/CBMiyAFBVV95cUxQTWVkN29DQ3Rz?oc=5"
        assert canonicalize_url(u) == "https://news.google.com/rss/articles/CBMiyAFBVV95cUxQTWVkN29DQ3Rz"

    def test_same_story_same_id(self):
        assert url_id("https://www.theverge.com/ai/1?utm_campaign=x") == url_id("https://theverge.com/ai/1/")


class TestTitleClustering:
    def test_reworded_headlines_match(self):
        assert titles_match("OpenAI launches GPT-6 with longer context",
                            "OpenAI launches GPT-6 with longer context, cheaper API", 85, 4)

    def test_different_stories_do_not_match(self):
        assert not titles_match("OpenAI launches GPT-6 with longer context",
                                "Google DeepMind releases Gemini 4 for robotics", 85, 4)

    def test_short_titles_only_match_exactly(self):
        assert not titles_match("OpenAI news", "OpenAI news roundup for the week ahead", 85, 4)

    def test_story_from_several_outlets_becomes_one_cluster(self):
        items = [
            item("Anthropic releases Claude 6 with a million-token context window", "https://techcrunch.com/claude-6"),
            item("Anthropic releases Claude 6 with million-token context window", "https://www.theverge.com/claude-6",
                 source="The Verge", published="2026-11-05T11:00:00Z"),
            item("Introducing Claude 6", "https://www.anthropic.com/news/claude-6", source="Anthropic",
                 authority=4, source_type="lab", published="2026-11-05T09:00:00Z"),
            # HN links straight to the lab post (with a tracking param): clusters by URL.
            item("Anthropic releases Claude 6 with a million-token context window",
                 "https://anthropic.com/news/claude-6/?utm_source=hn", source="Hacker News", authority=1,
                 source_type="community"),
            item("Mistral raises new funding round at higher valuation", "https://techcrunch.com/mistral"),
        ]
        created, _ = cluster_items(items, [], LIMITS)
        groups = sorted(sorted(s["name"] for s in c["sources"]) for c in created)
        assert groups == [["Anthropic", "Hacker News"], ["TechCrunch"], ["TechCrunch", "The Verge"]]
        lab = next(c for c in created if c["sources"][0]["name"] == "Anthropic")
        assert lab["url"] == "https://www.anthropic.com/news/claude-6" and lab["source_type"] == "lab"

    def test_lab_joins_later_by_url_and_becomes_primary_with_stable_id(self):
        hn = item("Anthropic releases Claude 6 with a million-token context window", "https://www.anthropic.com/news/claude-6",
                  source="Hacker News", authority=1, source_type="community")
        hn["extra"] = {"hn_url": "https://news.ycombinator.com/item?id=1"}
        lab = item("Introducing Claude 6", "https://anthropic.com/news/claude-6/", source="Anthropic",
                   authority=4, source_type="lab")
        created, _ = cluster_items([hn], [], LIMITS)
        cid = created[0]["id"]
        created2, touched = cluster_items([lab], created, LIMITS)
        assert created2 == [] and len(touched) == 1
        c = touched[0]
        assert c["id"] == cid and c["source_type"] == "lab"
        assert [s["name"] for s in c["sources"]] == ["Anthropic", "Hacker News"]
        assert c["sources"][1]["discussion"] == "https://news.ycombinator.com/item?id=1"
        # Re-adding the same outlet is a no-op.
        assert cluster_items([lab], created, LIMITS) == ([], [])

    def test_new_coverage_joins_existing_cluster_and_lab_takes_primary(self):
        existing, _ = cluster_items([item("Meta open-sources Llama 5 model family for developers",
                                          "https://techcrunch.com/llama-5")], [], LIMITS)
        lab = item("Meta open-sources Llama 5 model family for developers today", "https://ai.meta.com/blog/llama-5",
                   source="Meta AI", authority=4, source_type="lab", published="2026-11-05T08:00:00Z")
        created, touched = cluster_items([lab], existing, LIMITS)
        assert created == [] and len(touched) == 1
        c = touched[0]
        assert c["url"] == "https://ai.meta.com/blog/llama-5"
        assert c["source_type"] == "lab"
        assert [s["name"] for s in c["sources"]] == ["Meta AI", "TechCrunch"]
        assert c["published"] == "2026-11-05T08:00:00Z"

    def test_outside_time_window_stays_separate(self):
        a = item("Nvidia unveils new Blackwell Ultra GPU for AI data centers", "https://a.com/1",
                 published="2026-11-01T00:00:00Z")
        b = item("Nvidia unveils new Blackwell Ultra GPU for AI data centers", "https://b.com/1", source="B",
                 published="2026-11-05T00:00:00Z")
        created, _ = cluster_items([a, b], [], LIMITS)
        assert len(created) == 2

    def test_templated_titles_do_not_cluster(self):
        a = item("org/model-a is trending on Hugging Face", "https://huggingface.co/org/model-a",
                 source="HF", source_type="model")
        b = item("org/model-b is trending on Hugging Face", "https://huggingface.co/org/model-b",
                 source="HF2", source_type="model")
        created, _ = cluster_items([a, b], [], LIMITS)
        assert len(created) == 2
