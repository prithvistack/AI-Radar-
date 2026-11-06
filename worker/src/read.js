// GET /read?url= : clean article view for the site's reader pane.
// Only URLs from the live items.json (or arXiv) are fetched, so this can't be used as an open proxy.

import { Readability } from "@mozilla/readability";
import { DOMParser as XMLParser, parseHTML } from "linkedom";
import { isFetchable, json, sha1 } from "./util.js";

const READ_TTL = 7 * 24 * 3600;
const FAIL_TTL = 3600;
const ALLOW_TTL_MS = 10 * 60 * 1000;
const MAX_HTML = 1_500_000;
const MAX_TEXT = 400_000;
const BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36";

// ---------- Allowlist ----------
let allowCache = { at: 0, urls: null };

function normUrl(u) {
  try { const p = new URL(u); p.hash = ""; return p.href; } catch { return null; }
}

async function allowedUrls(env) {
  if (allowCache.urls && Date.now() - allowCache.at < ALLOW_TTL_MS) return allowCache.urls;
  try {
    const res = await fetch(`${env.SITE_URL.replace(/\/$/, "")}/data/items.json`, { cf: { cacheTtl: 300 } });
    if (!res.ok) throw new Error(`items.json HTTP ${res.status}`);
    const data = await res.json();
    const urls = new Set();
    for (const it of data.items || []) {
      for (const u of [it.url, ...(it.sources || []).map(s => s.url)]) {
        const n = normUrl(u);
        if (n) urls.add(n);
      }
    }
    allowCache = { at: Date.now(), urls };
  } catch (err) {
    console.warn("allowlist refresh failed", String(err));
    if (!allowCache.urls) return new Set();
  }
  return allowCache.urls;
}

export function arxivId(u) {
  try {
    const p = new URL(u);
    if (!/^(www\.)?arxiv\.org$/.test(p.hostname)) return null;
    const m = p.pathname.match(/^\/(?:abs|pdf|html)\/([\w.\/-]+?)(?:v\d+)?(?:\.pdf)?\/?$/);
    return m ? m[1] : null;
  } catch { return null; }
}

// ---------- Handler ----------
export async function handleRead(request, env, ctx) {
  const target = new URL(request.url).searchParams.get("url") || "";
  const norm = normUrl(target);
  if (!norm || !isFetchable(norm)) return json({ ok: false, reason: "bad-url" }, 400);
  if (!arxivId(norm) && !(await allowedUrls(env)).has(norm)) {
    return json({ ok: false, reason: "not-allowed", message: "Only stories from the current feed can be opened here." }, 403);
  }
  const article = await readArticle(norm, env, ctx);
  return json(article, 200, { "Cache-Control": "private, max-age=300" });
}

// Shared with /chat: returns the cached or freshly extracted article.
export async function readArticle(url, env, ctx) {
  const id = arxivId(url);
  const cacheKey = `read:v1:${await sha1(id ? `arxiv:${id}` : url)}`;
  const cached = await env.RADAR_KV.get(cacheKey, "json");
  if (cached) return cached;

  const candidates = id ? [`https://arxiv.org/html/${id}`, `https://arxiv.org/abs/${id}`] : [url];
  let result = { ok: false, reason: "fetch-failed", site: hostOf(url) };
  for (const candidate of candidates) {
    result = await extract(candidate, env);
    if (result.ok) break;
  }
  if (!result.ok && !id) {
    // Some sites (e.g. Ars Technica) put a bot wall in front of articles but publish full text in RSS.
    const fromFeed = await fromFullTextFeed(url, env);
    if (fromFeed) result = fromFeed;
  }
  ctx.waitUntil(env.RADAR_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: result.ok ? READ_TTL : FAIL_TTL }));
  return result;
}

const hostOf = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };

// ---------- Extraction ----------
const PAYWALL = /(subscribe to (continue|read)|to continue reading|already a subscriber|subscribers only|this article is for subscribers|sign in to read|create a free account to continue|log in to continue)/i;

async function extract(url, env) {
  let res;
  try {
    res = await fetch(url, {
      headers: { "User-Agent": BROWSER_UA, Accept: "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9" },
      redirect: "follow",
      signal: AbortSignal.timeout(parseInt(env.FETCH_TIMEOUT_MS || "12000", 10)),
    });
  } catch (err) {
    return { ok: false, reason: "fetch-failed", site: hostOf(url) };
  }
  const finalUrl = res.url || url;
  if (!res.ok) return { ok: false, reason: res.status === 403 || res.status === 401 ? "blocked" : `http-${res.status}`, site: hostOf(finalUrl) };
  if (!(res.headers.get("Content-Type") || "").includes("html")) return { ok: false, reason: "not-html", site: hostOf(finalUrl) };
  const html = (await res.text()).slice(0, 4_000_000);

  let doc;
  try { ({ document: doc } = parseHTML(html)); } catch { return { ok: false, reason: "parse-failed", site: hostOf(finalUrl) }; }
  const meta = pageMeta(doc, html);
  let article = null;
  try { article = new Readability(doc, { charThreshold: 400, keepClasses: false }).parse(); } catch (err) {
    console.warn("readability failed", url, String(err));
  }
  const site = article?.siteName || meta.site || hostOf(finalUrl);
  if (!article?.content) return { ok: false, reason: "no-article", site, title: meta.title };

  const text = (article.textContent || "").replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n\n").trim().slice(0, MAX_TEXT);
  if (text.length < 500) return { ok: false, reason: PAYWALL.test(html) ? "paywall" : "too-short", site, title: article.title || meta.title };
  if (PAYWALL.test(text.slice(-1500)) && text.length < 3000) return { ok: false, reason: "paywall", site, title: article.title || meta.title };

  // On arXiv HTML, Readability tends to pick a citation ("Chen et al. (2021)") as the byline.
  const byline = arxivId(finalUrl) ? meta.byline : article.byline || meta.byline;
  return {
    ok: true,
    url: finalUrl,
    title: article.title || meta.title || "",
    byline: (byline || "").trim() || null,
    published: article.publishedTime || meta.published || null,
    site,
    content_html: sanitize(article.content, finalUrl).slice(0, MAX_HTML),
    text,
  };
}

// FULLTEXT_FEEDS: JSON map of host -> RSS feed whose <content:encoded> has the full article.
async function fromFullTextFeed(url, env) {
  let feeds;
  try { feeds = JSON.parse(env.FULLTEXT_FEEDS || "{}"); } catch { return null; }
  const feedUrl = feeds[hostOf(url)];
  if (!feedUrl) return null;
  try {
    const res = await fetch(feedUrl, { headers: { "User-Agent": BROWSER_UA }, signal: AbortSignal.timeout(10000) });
    if (!res.ok) return null;
    const xml = new XMLParser().parseFromString(await res.text(), "text/xml");
    const want = normUrl(url);
    for (const item of xml.querySelectorAll("item")) {
      const link = item.querySelector("link")?.textContent?.trim();
      if (normUrl(link) !== want) continue;
      const body = [...item.childNodes].find(n => n.nodeName?.toLowerCase() === "content:encoded")?.textContent || "";
      if (body.length < 500) return null;
      const { document } = parseHTML(`<!doctype html><html><body>${body}</body></html>`);
      const text = (document.body.textContent || "").replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n\n").trim();
      const creator = [...item.childNodes].find(n => n.nodeName?.toLowerCase() === "dc:creator")?.textContent?.trim();
      const pub = item.querySelector("pubDate")?.textContent?.trim();
      return {
        ok: true,
        url,
        title: item.querySelector("title")?.textContent?.trim() || "",
        byline: creator || null,
        published: pub ? new Date(pub).toISOString() : null,
        // "AI - Ars Technica" -> "Ars Technica"
        site: xml.querySelector("channel > title")?.textContent?.split(/\s[-–|]\s/).pop()?.trim() || hostOf(url),
        content_html: sanitize(body, url).replace(/<a [^>]*>\s*(Read full article|Comments)\s*<\/a>/gi, "").slice(0, MAX_HTML),
        text: text.replace(/\s*Read full article\s*Comments\s*$/i, "").slice(0, MAX_TEXT),
        via: "rss",
        // Many feeds only carry an excerpt; the reader says so and points to the original.
        partial: text.length < 3000 || /Read full article/i.test(text),
      };
    }
  } catch (err) {
    console.warn("feed fallback failed", feedUrl, String(err));
  }
  return null;
}

function pageMeta(doc, html) {
  const m = (...names) => {
    for (const n of names) {
      const el = doc.querySelector(`meta[property="${n}"]`) || doc.querySelector(`meta[name="${n}"]`);
      const v = el?.getAttribute("content");
      if (v) return v.trim();
    }
    return null;
  };
  const ld = html.match(/"datePublished"\s*:\s*"([^"]+)"/);
  return {
    title: m("og:title", "twitter:title", "citation_title") || doc.querySelector("title")?.textContent?.trim() || null,
    site: m("og:site_name", "application-name"),
    byline: m("author", "article:author", "citation_author"),
    published: m("article:published_time", "citation_publication_date", "citation_date", "date") || ld?.[1]
      || doc.querySelector("time[datetime]")?.getAttribute("datetime") || null,
  };
}

// ---------- Sanitizer (allowlist) ----------
const ALLOWED = new Set([
  "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "code", "em", "strong",
  "b", "i", "u", "s", "sup", "sub", "small", "mark", "a", "img", "figure", "figcaption", "table", "thead", "tbody",
  "tfoot", "tr", "th", "td", "caption", "dl", "dt", "dd", "abbr", "cite", "q", "time", "kbd", "var",
  // MathML (arXiv papers)
  "math", "semantics", "mrow", "mi", "mo", "mn", "ms", "msup", "msub", "msubsup", "mfrac", "msqrt", "mroot", "mtext",
  "mspace", "mover", "munder", "munderover", "mtable", "mtr", "mtd", "mstyle", "mpadded", "mphantom", "menclose",
]);
// Removed with their contents (everything else not allowed is unwrapped, keeping its text).
const DROP = new Set([
  "script", "style", "iframe", "frame", "frameset", "object", "embed", "applet", "form", "input", "button", "select",
  "textarea", "option", "noscript", "template", "link", "meta", "base", "svg", "canvas", "video", "audio", "source",
  "track", "picture", "dialog", "annotation", "annotation-xml", "head", "title", "nav", "aside", "footer",
]);
const ATTRS = {
  a: ["href", "title"], img: ["src", "alt", "title", "width", "height"], td: ["colspan", "rowspan"],
  th: ["colspan", "rowspan", "scope"], ol: ["start"], time: ["datetime"], abbr: ["title"], math: ["display"],
  mo: ["stretchy", "fence", "separator"], mi: ["mathvariant"], mspace: ["width"],
};
const TRACKER = /(doubleclick|google-analytics|googletagmanager|facebook\.com\/tr|scorecardresearch|quantserve|chartbeat|pixel\.|\/pixel|beacon|omtrdc|amazon-adsystem|adsrvr|taboola|outbrain|parsely|newrelic)/i;

function absolute(u, base) {
  try { return new URL(u, base).href; } catch { return null; }
}

export function sanitize(html, baseUrl) {
  const { document } = parseHTML(`<!doctype html><html><body><div id="__root">${html}</div></body></html>`);
  const root = document.getElementById("__root");
  walk(root, baseUrl);
  return root.innerHTML;
}

function walk(node, base) {
  for (const child of [...node.childNodes]) {
    if (child.nodeType === 8) { child.remove(); continue; } // comments
    if (child.nodeType !== 1) continue;
    const tag = (child.localName || child.tagName || "").toLowerCase();
    if (DROP.has(tag)) { child.remove(); continue; }
    walk(child, base);
    if (!ALLOWED.has(tag)) { child.replaceWith(...child.childNodes); continue; }
    cleanAttrs(child, tag, base);
  }
}

function cleanAttrs(el, tag, base) {
  const keep = ATTRS[tag] || [];
  const lazySrc = tag === "img"
    ? (el.getAttribute("data-src") || el.getAttribute("data-lazy-src") || (el.getAttribute("srcset") || el.getAttribute("data-srcset") || "").split(",")[0].trim().split(/\s+/)[0])
    : null;
  for (const attr of [...el.attributes]) {
    if (!keep.includes(attr.name)) el.removeAttribute(attr.name);
  }
  if (tag === "a") {
    const href = absolute(el.getAttribute("href") || "", base);
    if (!href || !/^https?:/.test(href)) { el.replaceWith(...el.childNodes); return; }
    el.setAttribute("href", href);
    el.setAttribute("target", "_blank");
    el.setAttribute("rel", "noopener noreferrer nofollow");
  }
  if (tag === "img") {
    let src = el.getAttribute("src");
    if (!src || src.startsWith("data:")) src = lazySrc || src;
    const abs = src ? absolute(src, base) : null;
    const tiny = ["width", "height"].some(a => { const v = parseInt(el.getAttribute(a) || "", 10); return v > 0 && v <= 2; });
    if (!abs || !abs.startsWith("https:") || TRACKER.test(abs) || tiny) { el.remove(); return; }
    el.setAttribute("src", abs);
    el.setAttribute("loading", "lazy");
    el.setAttribute("referrerpolicy", "no-referrer");
    el.setAttribute("decoding", "async");
  }
}
