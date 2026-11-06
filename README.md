# AI Radar

A personal AI newspaper. Every 3 hours a GitHub Action scrapes trusted sources, clusters duplicate stories, has an LLM summarize and score them, and commits `site/data/items.json`. Cloudflare Pages serves the static site. A floating companion (a Cloudflare Worker using Groq/Gemini) explains anything you're reading.

```
scraper/   Python pipeline (fetch → skip seen → keyword filter → cluster → enrich → rank → store)
site/      Static newspaper + companion (plain HTML/CSS/JS)
worker/    Cloudflare Worker: /chat (streamed), /glossary (KV), /read (clean article view)
.github/workflows/update.yml   cron every 3h
```

## Setup (one time)

1. **Repo.** Create a **private** GitHub repo `ai-radar` and push this folder:
   ```sh
   git add -A && git commit -m "AI Radar"
   git remote add origin git@github.com:<you>/ai-radar.git
   git push -u origin main
   ```
2. **Free API keys.**
   - Groq: https://console.groq.com → API Keys
   - Gemini: https://aistudio.google.com → Get API key
3. **Scraper secrets.** GitHub repo → Settings → Secrets and variables → Actions → add `GEMINI` (scoring) and `GROQ` (backup).
   Then Actions → *Update feed* → **Run workflow** once. (`GITHUB_TOKEN` is built in.)
4. **Cloudflare Pages** (free account): Workers & Pages → Create → Pages → *Connect to Git* → pick the repo.
   Framework preset: *None*, build command: *(empty)*, **build output directory: `site`**.
   Note the URL it gives you (e.g. `https://ai-radar-xyz.pages.dev`).
5. **Worker** (from the `worker/` folder; `npx` uses the local wrangler, no global install needed):
   ```sh
   cd worker
   npm install
   npx wrangler login
   npx wrangler kv namespace create RADAR_KV     # paste the printed id into wrangler.toml
   ```
   In `wrangler.toml`, set `ALLOWED_ORIGINS` and `SITE_URL` to your Pages URL from step 4
   (`/read` only opens links listed in that site's `data/items.json`, plus arXiv). Then:
   ```sh
   npx wrangler secret put GROQ_API_KEY
   npx wrangler secret put GEMINI_API_KEY
   npx wrangler deploy
   ```
   Put the printed Worker URL into `site/config.js` (`WORKER_URL`), commit and push.
6. Open the site and tap the companion button. Done.

## Running locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r scraper/requirements.txt
cp .env.example .env      # then paste your keys into .env (it is never committed)
./dev.sh                  # site + reader + companion at http://localhost:8765; Ctrl+C stops them
.venv/bin/python scraper/main.py                  # refresh the feed (uses GROQ_API_KEY from .env)
.venv/bin/python scraper/main.py --dry-run        # fetch and print, write nothing
.venv/bin/python -m pytest -q scraper/tests
```
Restart `./dev.sh` after changing `.env`. On localhost, `site/config.js` uses the local Worker automatically.

## Tuning

Everything lives in `scraper/config.yaml`: sources, keywords, thresholds (HF upvotes, GitHub stars, HN points), clustering similarity, LLM model names and the per-run call cap, ranking weights. Worker model names, routing threshold and the daily chat limit are `[vars]` in `worker/wrangler.toml`.

## Ranking and credibility

Each story gets an LLM judgment (`significance` 1–10, `substance`, `hype`) from a deliberately skeptical
"senior editor" prompt, plus a credibility weight from `credibility:` in `scraper/config.yaml`:

- **Tiers**: tier 1 = 1.0 (Reuters, MIT TR, Ars, HF Papers, HN), tier 2 = 0.75 (TechCrunch, The Verge, VentureBeat), anything else 0.45.
- **First-party** (lab/company blogs, GitHub releases, HF model cards, company newsrooms and press wires): 0.7 alone; once an independent outlet covers the story it takes that outlet's weight and its claim status becomes "independently verified" (otherwise "company-reported").
- **Same-owner rule**: an outlet writing about its owner, subsidiary or major investor (`same_owner:` list) counts as first-party.

`score = significance × weight × (1 + 0.3·log2(1 + independent_sources)) × 0.5 if hype × 0.4 if no substance × 0.5^(age_h/36)`.
A story is **Top tier** with significance ≥ 7, substance, no hype, and a tier-1 or independent source (thresholds under `ranking.top`).
Without `GROQ_API_KEY`, heuristics cap significance at 5, so nothing reaches the top tier until the LLM runs.

## The paper

The site is a newspaper you turn page by page (`site/paper.js` decides what goes where, `site/app.js` prints it). Swipe (two fingers sideways on a trackpad), use the arrow keys, click the page corner or use the bar at the bottom.

- **Front page**: up to 9 stories from the last 48 hours, top tier first, led by the first one with a picture.
- **Under the Radar**: significant stories (significance ≥ 5, substance, no hype, not first-party) that only one outlet has.
- **Models & Tools, Research, Products, Business, Policy & Power**: the 9 best remaining stories in each.
- **Other Stories**: everything left, in brief. No story is printed twice; hype-flagged stories only appear here, labelled.

Similar headlines are grouped in the browser, so this uses no API requests.

**Cover images** (`scraper/images.py`) come from the feed entry, else the publisher's `og:image`, else other outlets on the same story; arXiv papers use their first figure. Only the URL is stored and the browser loads the picture from the publisher, credited underneath. Logos and any image shared by 3 or more stories (site defaults) are rejected. A lead with no picture gets a printed category plate; other stories simply run as text.

## Companion

It explains whatever you have open and answers questions about the paper. The site sends it a list of stories in printed order (`brief()` in `site/app.js`), so "top 5" means the front page; stories matching the question are added, so "anything on Anthropic?" reaches the whole feed. To save tokens, the list goes out only when no article is open or the question sounds like a news question.

## Reader

Tapping a card opens the article inside the site. The Worker's `/read` fetches the page, extracts it with
Readability (linkedom), sanitizes it (allowlisted tags/attributes, no scripts/iframes/trackers) and caches it in KV
for 7 days. arXiv uses the full HTML paper when available. If a site blocks it or it's paywalled, the reader shows
the summary and an "Open original" button. `FULLTEXT_FEEDS` (in `wrangler.toml`) maps a host to an RSS feed to use
when the page itself is blocked; Ars Technica's feed only carries an excerpt, and the reader says so.

## Notes on sources

All feed URLs were checked on 2026-11-05. Changes from the original plan:
- **Meta AI**: `ai.meta.com/blog` rejects non-browser clients, so it uses Meta's own RSS (Engineering at Meta AI-research feed, plus Meta Newsroom with the AI keyword filter).
- **Mistral**: has an RSS feed (`mistral.ai/news/rss`), so it's used instead of page scraping.
- **AI4Bharat**: the blog is rendered entirely in the browser (no links, RSS or sitemap), so it's tracked through a Google News query.
- **Anthropic, Sarvam**: scraped from their index pages (`html_links.py`); posts without a publish date (category pages) are skipped.
- **Reddit** blocks most cloud IPs; it's marked optional and fails quietly.
- **llama.cpp** tags builds (`b6543`) rather than semver, so the "major/minor releases only" rule rarely matches it.
- Google News links are opaque redirects, so `scraper/resolve.py` turns them into publisher URLs and fetches the publisher's own description. It uses an undocumented Google endpoint; if that breaks, stories keep their Google link (they still appear, but can't open in the reader).
- **Reuters** blocks automated readers, so its stories show a headline only and open through "Open original". The LLM only sees that headline too, so it can score them but cannot add detail.

## Free-tier budget

- Scoring uses Gemini Flash-Lite (`llm.model`): at most `max_calls_per_run` (4) calls × 8 runs/day. Free Gemini quotas are per model and per day, and `gemini-3.6-flash` allows only 20 requests, so that one is kept for the companion's long papers. Current numbers: https://aistudio.google.com/rate-limit.
- Groq is the scoring backup and handles short chats (1,000 requests/day, 8K tokens/min, so its calls are paced 32s apart). Unscored items are picked up on later runs; `--max-llm-calls N` clears a backlog, and `--rescore` re-scores stored stories after a prompt or model change.
- Cloudflare Pages: one deploy per feed update, about 250/month (limit 500).
- Cover images: page fetches only, up to 150 per run, no LLM calls.
- Workers KV: one write per chat (daily counter) plus page caching; well under 1,000 writes/day at the default `DAILY_CHAT_LIMIT=300`.
- GitHub disables scheduled workflows after 60 days without repo activity; the bot's commits count as activity.

## Privacy

The site has no login but is unlisted (`noindex` meta, `X-Robots-Tag` header, `robots.txt` disallow-all). The companion has no password: chat and glossary only accept requests with an `Origin` in `ALLOWED_ORIGINS`, and `DAILY_CHAT_LIMIT` caps daily use. That stops other websites, but a script that fakes the header could still use up the daily limit or edit the glossary. API keys live only in GitHub Actions secrets and Worker secrets.
