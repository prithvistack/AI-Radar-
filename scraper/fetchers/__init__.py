from . import github, hackernews, html_links, huggingface, reddit, rss

FETCHERS = {
    "rss": rss.fetch,
    "html_links": html_links.fetch,
    "hf_papers": huggingface.fetch_papers,
    "hf_models": huggingface.fetch_models,
    "github_repos": github.fetch_repos,
    "github_releases": github.fetch_releases,
    "hackernews": hackernews.fetch,
    "reddit": reddit.fetch,
}
