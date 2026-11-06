// AI Radar site: loads data/items.json, prints it as a newspaper (paper.js decides what goes where),
// turns pages, and opens articles in the reader. Talks to the companion through DOM events.
(() => {
  "use strict";

  const CFG = window.RADAR_CONFIG || {};
  const OTHER_PAGE_SIZE = 60;
  const SWIPE_DISTANCE = 50; // px of sideways trackpad travel that counts as a page turn
  const CATEGORY_LABEL = {
    "model-release": "Models", research: "Research", tool: "Tools", product: "Products",
    business: "Business", policy: "Policy", other: "Briefly",
  };
  const P = window.RadarPaper;

  const $ = (sel, root = document) => root.querySelector(sel);
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  };
  function el(tag, props = {}, ...kids) {
    const n = Object.assign(document.createElement(tag), props);
    n.append(...kids.filter(k => k !== null && k !== undefined && k !== false));
    return n;
  }
  const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));
  const ask = (item, question, term) => emit("radar:ask", { item, question, term });

  const state = {
    byId: new Map(), leads: [], pages: [], updated: null, shownOther: OTHER_PAGE_SIZE,
    lastVisit: store.get("radar.lastVisit", null),
  };

  function timeAgo(iso) {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    const d = Math.floor(s / 86400);
    return d === 1 ? "1 day ago" : `${d} days ago`;
  }
  function safeUrl(u) {
    try { const p = new URL(u); return p.protocol === "https:" || p.protocol === "http:" ? p.href : "#"; }
    catch { return "#"; }
  }
  // Some feeds' "summary" is only the headline again; show nothing rather than repeat it.
  const plain = s => (s || "").toLowerCase().replace(/\W+/g, " ").trim();
  function summaryOf(it) {
    const text = it.summary || it.raw_summary || "";
    return plain(text).startsWith(plain(it.title)) ? "" : text;
  }
  // A blocked lead (Reuters has no text) borrows the summary of another outlet covering the same story.
  const storySummary = it => [it, ...(it._also || [])].map(summaryOf).find(Boolean) || "";

  // ---------- Badges ----------
  const BADGE_INFO = {
    "first-party": "Published by the company itself (its blog, release notes, model card or a press release), or by an outlet that owns, is owned by, or invests in the company it covers. Expect it to be promotional.",
    "company-reported": "Only the company has reported this so far. Performance and quality claims are theirs and haven't been checked by anyone independent.",
    "verified": "At least one independent outlet has reported this story. That confirms the news happened; it doesn't mean every benchmark claim was tested.",
    "hype": "Flagged for vague claims, superlatives without data, or clickbait framing. Ranked lower.",
    "coverage": "Distinct publishers not owned by or tied to the company involved. Each extra independent outlet raises the ranking a little.",
  };
  function badges(it, { coverage = true } = {}) {
    const box = el("div", { className: "badges" });
    const add = (key, label, cls) => {
      const b = el("button", { className: `badge ${cls}`, textContent: label, ariaLabel: `${label}: what does this mean?` });
      b.dataset.info = BADGE_INFO[key];
      box.append(b);
    };
    if (it.hype) add("hype", "Hype", "b-hype");
    if (it.first_party) add("first-party", "First-party", "b-first");
    if (it.claim_status === "company-reported") add("company-reported", "Company-reported", "b-warn");
    if (it.claim_status === "independently-verified") add("verified", "Independently verified", "b-ok");
    const n = it.independent_sources || 0;
    if (coverage && n > 0) add("coverage", `${n} independent ${n === 1 ? "source" : "sources"}`, "b-cov");
    return box.children.length ? box : null;
  }
  const pop = $("#badge-pop");
  document.addEventListener("click", e => {
    const badge = e.target.closest(".badge");
    if (!badge) { pop.hidden = true; return; }
    e.stopPropagation(); e.preventDefault();
    if (!pop.hidden && pop._for === badge) { pop.hidden = true; return; }
    pop._for = badge;
    pop.textContent = badge.dataset.info;
    pop.hidden = false;
    const r = badge.getBoundingClientRect(), w = Math.min(300, innerWidth - 32);
    pop.style.width = `${w}px`;
    pop.style.left = `${Math.min(Math.max(16, r.left), innerWidth - w - 16)}px`;
    pop.style.top = `${r.bottom + 8}px`;
  }, true);

  // ---------- Pictures ----------
  function plate(it) {
    return el("div", { className: "plate", ariaHidden: "true" }, el("span", { textContent: CATEGORY_LABEL[it.category] || "AI" }));
  }
  function cover(it, { fallback = false } = {}) {
    const c = P.coverOf(it);
    if (!c) return fallback ? plate(it) : null;
    const img = el("img", { src: c.src, alt: "", loading: "lazy", decoding: "async", referrerPolicy: "no-referrer" });
    const fig = el("figure", { className: `cover${c.figure ? " is-figure" : ""}` }, img,
      el("figcaption", { textContent: c.figure ? "Figure from the paper" : `Image: ${c.credit}` }));
    const drop = () => fig.isConnected && (fallback ? fig.replaceWith(plate(it)) : fig.remove());
    img.addEventListener("error", drop);
    img.addEventListener("load", () => { if (img.naturalWidth < 240) drop(); });
    return fig;
  }

  // ---------- Stories ----------
  function byline(it) {
    const also = (it._also || []).length;
    return el("p", { className: "byline" },
      el("b", { textContent: it.sources[0]?.name || "" }),
      ` · ${timeAgo(it.published)}`, also ? ` · ${also + 1} reports` : null);
  }
  function headline(it, tag) {
    const a = el("a", { href: safeUrl(it.url), textContent: it.title, target: "_blank", rel: "noopener noreferrer" });
    a.addEventListener("click", e => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      Reader.open(it);
    });
    return el(tag, { className: "headline" }, a);
  }
  // size: lead | major | minor | brief
  function story(it, size, { picture = size !== "brief", kicker = true, note = null, deck = size !== "brief", fallback = false } = {}) {
    const fresh = state.lastVisit && it.fetched > state.lastVisit;
    const text = storySummary(it);
    const node = el("article", { className: `story s-${size}` },
      picture ? cover(it, { fallback }) : null,
      kicker ? el("p", { className: "kicker" }, fresh ? el("em", { textContent: "New" }) : null, CATEGORY_LABEL[it.category] || "Briefly") : null,
      headline(it, size === "lead" ? "h2" : "h3"),
      deck && text ? el("p", { className: "deck", textContent: text }) : null,
      note ? el("p", { className: "note", textContent: note }) : null,
      byline(it),
      badges(it, { coverage: size === "lead" || size === "major" }));
    node.dataset.id = it.id;
    node.addEventListener("click", e => {
      if (e.target.closest("a, button") || getSelection()?.toString()) return;
      Reader.open(it);
    });
    return node;
  }

  // ---------- Pages ----------
  const dateline = () => new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  function editionName() {
    const h = new Date(state.updated).getHours();
    return h < 5 ? "Late Edition" : h < 12 ? "Morning Edition" : h < 17 ? "Afternoon Edition" : "Evening Edition";
  }
  function turnPrompt(i) {
    const next = state.pages[i + 1];
    if (!next) return el("button", { className: "turn", onclick: () => goTo(0) },
      el("span", { className: "turn-label", textContent: "That's the whole paper" }),
      el("span", { className: "turn-title", textContent: "Back to the front page" }));
    return el("button", { className: "turn", onclick: () => goTo(i + 1) },
      el("span", { className: "turn-label", textContent: `Turn to page ${i + 2}` }),
      el("span", { className: "turn-title", textContent: next.title }),
      el("span", { className: "turn-hint", textContent: next.stories[0].title }));
  }
  function runningHead(i) {
    return el("header", { className: "running" }, el("span", { className: "running-name", textContent: "The AI Radar" }),
      el("span", { textContent: dateline() }), el("span", { textContent: `Page ${i + 1}` }));
  }

  function frontPage(page) {
    const [lead, ...rest] = page.stories;
    const col = list => list.map(it => story(it, "minor", { picture: false }));
    const inside = el("nav", { className: "inside", ariaLabel: "Inside this edition" },
      el("h3", { textContent: "Inside" }),
      ...state.pages.slice(1).map((p, i) => el("button", { onclick: () => goTo(i + 1) },
        el("span", { textContent: p.title }), el("b", { textContent: i + 2 }))));
    return [
      el("header", { className: "masthead" },
        el("div", { className: "ears" },
          el("p", { className: "ear", textContent: "Scanned every three hours. Skeptical by design." }),
          el("p", { className: "ear right", textContent: `${state.byId.size} reports reviewed for this edition` })),
        el("h1", { className: "nameplate", textContent: "The AI Radar" }),
        el("div", { className: "dateline" }, el("span", { textContent: editionName() }),
          el("span", { textContent: dateline() }), el("span", { textContent: `Updated ${timeAgo(state.updated)}` }))),
      el("div", { className: "front-grid" },
        el("div", { className: "front-lead" }, story(lead, "lead", { fallback: true })),
        el("div", { className: "front-left" }, ...col(rest.slice(0, 3))),
        el("div", { className: "front-right" }, ...rest.slice(3, 4).map(it => story(it, "major")), ...col(rest.slice(4, 5)))),
      el("div", { className: "front-lower" }, ...rest.slice(5, 8).map(it => story(it, "major")), inside),
    ];
  }

  function sectionPage(page, i) {
    const [lead, ...rest] = page.stories;
    const radar = page.id === "radar";
    const opts = it => radar ? { kicker: true, note: `Only ${P.outlet(it)} has this so far.` } : { kicker: false };
    const pictured = rest.filter(P.coverOf).slice(0, 3);
    const briefs = rest.filter(it => !pictured.includes(it));
    return [
      runningHead(i),
      el("header", { className: `section-head${radar ? " inverse" : ""}` },
        el("h2", { textContent: page.title }), el("p", { textContent: page.dek })),
      el("div", { className: "section-lead" }, story(lead, "lead", { ...opts(lead), fallback: true })),
      pictured.length ? el("div", { className: "section-row" }, ...pictured.map(it => story(it, "major", opts(it)))) : null,
      briefs.length ? el("div", { className: "section-briefs" }, ...briefs.map(it => story(it, "minor", { ...opts(it), picture: false }))) : null,
    ];
  }

  function otherPage(page, i) {
    const list = el("div", { className: "briefs" });
    const more = el("button", { className: "plain-btn wide", textContent: "More stories" });
    const fill = () => {
      list.replaceChildren(...page.stories.slice(0, state.shownOther).map(it => story(it, "brief")));
      more.hidden = page.stories.length <= state.shownOther;
    };
    more.addEventListener("click", () => { state.shownOther += OTHER_PAGE_SIZE; fill(); });
    fill();
    return [
      runningHead(i),
      el("header", { className: "section-head" }, el("h2", { textContent: page.title }), el("p", { textContent: page.dek })),
      list, more,
      el("aside", { className: "colophon" },
        el("h3", { textContent: "How this paper is made" }),
        el("p", { textContent: "Every story is scored by a skeptical editor for significance and substance, then weighed by who reported it and how many independent outlets confirmed it. Company announcements are marked until someone else confirms them. Pictures come from the publishers credited under them." })),
    ];
  }

  function printPaper() {
    const book = $("#book");
    book.replaceChildren(...state.pages.map((p, i) => {
      const parts = p.kind === "front" ? frontPage(p) : p.kind === "other" ? otherPage(p, i) : sectionPage(p, i);
      return el("section", { className: `sheet${i ? "" : " is-front"}${p.id === "radar" ? " is-radar" : ""}`, hidden: true, ariaLabel: p.title },
        ...parts, turnPrompt(i), el("button", { className: "curl", ariaLabel: "Turn the page", onclick: () => goTo((i + 1) % state.pages.length) }));
    }));
    $("#page-tabs").replaceChildren(...state.pages.map((p, i) => el("button", {
      className: "page-tab", onclick: () => goTo(i) }, el("b", { textContent: i + 1 }), el("span", { textContent: p.title }))));
    current = Math.min(Math.max(0, (parseInt(location.hash.replace("#page-", ""), 10) || 1) - 1), state.pages.length - 1);
    book.children[current].hidden = false;
    syncTabs();
  }

  // ---------- Turning pages ----------
  let current = 0, turning = false;
  const calm = matchMedia("(prefers-reduced-motion: reduce)");
  function syncTabs() {
    const tabs = $("#page-tabs").children;
    [...tabs].forEach((t, i) => t.classList.toggle("active", i === current));
    tabs[current]?.scrollIntoView({ inline: "center", block: "nearest" });
    $("#prev-page").disabled = current === 0;
    $("#next-page").disabled = current === state.pages.length - 1;
    history.replaceState(history.state, "", current ? `#page-${current + 1}` : location.pathname);
  }
  function goTo(n) {
    if (turning || n === current || n < 0 || n >= state.pages.length) return;
    const sheets = $("#book").children, from = sheets[current], to = sheets[n], forward = n > current;
    current = n;
    to.hidden = false;
    if (forward) to.scrollTop = 0;
    syncTabs();
    if (calm.matches) { from.hidden = true; return; }

    // The upper leaf swings around the spine; whichever way we go, the page underneath stays still.
    const leaf = forward ? from : to, under = forward ? to : from;
    turning = true;
    leaf.classList.add("leaf"); under.classList.add("under");
    const open = "rotateY(0deg)", shut = "rotateY(-118deg)", dim = "brightness(.62)", lit = "brightness(1)";
    const timing = { duration: 720, easing: "cubic-bezier(.42,.02,.28,1)", fill: "both" };
    const swing = leaf.animate([{ transform: forward ? open : shut }, { transform: forward ? shut : open }], timing);
    const shadow = under.animate([{ filter: forward ? dim : lit }, { filter: forward ? lit : dim }], timing);
    swing.finished.catch(() => {}).then(() => {
      from.hidden = true;
      leaf.classList.remove("leaf"); under.classList.remove("under");
      swing.cancel(); shadow.cancel();
      turning = false;
    });
  }
  $("#prev-page").addEventListener("click", () => goTo(current - 1));
  $("#next-page").addEventListener("click", () => goTo(current + 1));

  const overlayOpen = () => !$("#reader").hidden || !$("#search-sheet").hidden || document.body.classList.contains("companion-open");
  addEventListener("keydown", e => {
    if (overlayOpen() || /INPUT|TEXTAREA/.test(document.activeElement?.tagName || "")) return;
    if (e.key === "ArrowRight") goTo(current + 1);
    if (e.key === "ArrowLeft") goTo(current - 1);
  });
  // Swipe sideways to turn, like a real page; vertical drags stay as scrolling.
  let touch = null;
  $("#book").addEventListener("touchstart", e => {
    touch = e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY, t: e.timeStamp } : null;
  }, { passive: true });
  $("#book").addEventListener("touchend", e => {
    if (!touch) return;
    const dx = e.changedTouches[0].clientX - touch.x, dy = e.changedTouches[0].clientY - touch.y;
    if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(dy) && e.timeStamp - touch.t < 700) goTo(current + (dx < 0 ? 1 : -1));
    touch = null;
  }, { passive: true });

  // Two fingers sideways on a trackpad turns one page; the gesture's momentum is ignored until it dies away.
  let swiped = 0, swipeDone = false, swipeEnd;
  addEventListener("wheel", e => {
    if (overlayOpen() || e.target.closest?.(".page-tabs") || Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
    e.preventDefault();
    clearTimeout(swipeEnd);
    swipeEnd = setTimeout(() => { swiped = 0; swipeDone = false; }, 160);
    if (swipeDone) return;
    swiped += e.deltaX;
    if (Math.abs(swiped) < SWIPE_DISTANCE) return;
    swipeDone = true;
    goTo(current + Math.sign(swiped));
  }, { passive: false });

  // ---------- Search ----------
  const sheet = $("#search-sheet"), input = $("#search");
  function searchable(it) {
    return it._search || (it._search = [it.title, it.summary, it.raw_summary, (it.key_terms || []).join(" "),
      it.sources.map(s => s.name).join(" "), it.category].join(" ").toLowerCase());
  }
  let searchTimer;
  function runSearch() {
    const q = input.value.trim().toLowerCase(), words = q.split(/\s+/);
    const hits = q ? state.leads.filter(l => [l, ...l._also].some(m => words.every(w => searchable(m).includes(w)))) : [];
    $("#search-count").textContent = q ? `${hits.length} ${hits.length === 1 ? "story" : "stories"}` : "";
    $("#search-results").replaceChildren(...hits.slice(0, 80).map(it => story(it, "brief")));
  }
  input.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 120); });
  function toggleSearch(open) { sheet.hidden = !open; if (open) input.focus(); }
  $("#open-search").addEventListener("click", () => toggleSearch(true));
  $("#close-search").addEventListener("click", () => toggleSearch(false));
  addEventListener("keydown", e => { if (e.key === "Escape" && !sheet.hidden && $("#reader").hidden) toggleSearch(false); });

  // ---------- Day and night editions ----------
  const savedTheme = store.get("radar.theme", null);
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("radar.theme", next);
  });

  // "New since last visit": remember when this visit started, once the page has been seen.
  const visitStart = new Date().toISOString();
  const saveVisit = () => store.set("radar.lastVisit", visitStart);
  addEventListener("pagehide", saveVisit);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") saveVisit(); });

  // ---------- Reader ----------
  const Reader = (() => {
    const pane = $("#reader"), body = $("#reader-body"), scroller = $("#reader-scroll");
    let current = null, pushed = false, reqId = 0;
    const api = (CFG.WORKER_URL || "").replace(/\/$/, "");

    // The Worker already sanitizes; this is a second, client-side pass before insertion.
    function clean(html) {
      const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
      doc.querySelectorAll("script, style, iframe, object, embed, form, link, meta, base").forEach(n => n.remove());
      doc.querySelectorAll("*").forEach(n => {
        for (const a of [...n.attributes]) {
          if (/^on/i.test(a.name) || a.name === "style" || (/^(href|src)$/i.test(a.name) && !/^https?:/i.test(a.value))) n.removeAttribute(a.name);
        }
        if (n.tagName === "A") { n.target = "_blank"; n.rel = "noopener noreferrer nofollow"; }
        if (n.tagName === "IMG") { n.loading = "lazy"; n.referrerPolicy = "no-referrer"; }
      });
      return doc.body.firstChild;
    }
    // Readability often keeps the article's own hero, in which case ours would repeat it.
    function opensWithImage(content) {
      const img = content.querySelector("img");
      const para = [...content.querySelectorAll("p")].find(p => p.textContent.length > 80);
      return !!img && (!para || !!(img.compareDocumentPosition(para) & Node.DOCUMENT_POSITION_FOLLOWING));
    }
    function header(it, art, group = it) {
      const when = art?.published ? new Date(art.published) : new Date(it.published);
      const meta = [art?.byline, art?.site || it.sources[0]?.name, isNaN(when) ? null : when.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" })]
        .filter(Boolean).join(" · ");
      return [
        el("p", { className: "kicker", textContent: CATEGORY_LABEL[it.category] || "Briefly" }),
        el("h1", { textContent: art?.title || it.title }),
        el("p", { className: "reader-meta", textContent: meta }),
        badges(group),
        cover(group),
      ].filter(Boolean);
    }
    // Key terms, other outlets on the story and paper links go at the foot of the article.
    function footer(it) {
      const foot = el("footer", { className: "reader-foot" });
      if (it.key_terms?.length) foot.append(el("h3", { textContent: "In this story" }),
        el("div", { className: "terms" }, ...it.key_terms.map(t => el("button", {
          className: "term", textContent: t, title: `Ask the companion about "${t}"`,
          onclick: () => ask(it, `What is "${t}", and how is it used here?`, t) }))));
      const reports = [
        ...it.sources.slice(1).map(s => el("li", {}, el("b", { textContent: s.name }),
          el("a", { href: safeUrl(s.discussion || s.url), target: "_blank", rel: "noopener noreferrer", textContent: s.title || s.url }))),
        ...(it._also || []).map(a => el("li", {}, el("b", { textContent: a.sources[0]?.name || "" }),
          el("button", { textContent: a.title, onclick: () => open(a) }))),
      ];
      if (reports.length) foot.append(el("h3", { textContent: "Also reported" }), el("ul", { className: "also" }, ...reports));
      const ex = it.extra || {}, links = [];
      if (ex.arxiv_id) links.push([`https://arxiv.org/abs/${ex.arxiv_id}`, "arXiv"]);
      if (ex.pdf) links.push([ex.pdf, "PDF"]);
      if (ex.hn_url) links.push([ex.hn_url, `Hacker News${ex.points ? ` · ${ex.points} points` : ""}`]);
      if (links.length) foot.append(el("p", { className: "links" }, ...links.map(([href, label]) =>
        el("a", { href: safeUrl(href), target: "_blank", rel: "noopener noreferrer", textContent: `${label} ↗` }))));
      return foot.children.length ? foot : null;
    }
    function fallback(it, reason) {
      const why = {
        paywall: "This article looks paywalled.", blocked: "The site blocked the reader.",
        "not-allowed": "This link isn't in the current feed.", offline: "The reader service isn't reachable.",
      }[reason] || "Couldn't extract a clean article from this page.";
      const text = storySummary(it);
      body.replaceChildren(...header(it), el("div", { className: "reader-fallback" },
        el("p", { className: "reader-note", textContent: text ? `${why} Here's the summary instead.` : `${why} No summary is available yet, so use the original.` }),
        text ? el("p", { className: "reader-summary", textContent: text }) : null,
        el("a", { className: "plain-btn", href: safeUrl(it.url), target: "_blank", rel: "noopener noreferrer", textContent: "Open original ↗" })),
        footer(it) || "");
    }

    async function open(it) {
      if (!pane.hidden && current?.id === it.id) return;
      if (pane.hidden) { history.pushState({ reader: it.id }, ""); pushed = true; }
      current = it;
      emit("radar:context", { item: it }); // the companion explains whatever is open
      pane.hidden = false;
      document.body.classList.add("reader-open");
      requestAnimationFrame(() => pane.classList.add("open"));
      $("#reader-source").textContent = it.sources[0]?.name || "";
      $("#reader-original").href = safeUrl(it.url);
      scroller.scrollTop = 0;
      body.replaceChildren(...header(it), el("div", { className: "reader-loading" }, el("i"), el("i"), el("i"), el("i")));
      $("#reader-back").focus({ preventScroll: true });

      const mine = ++reqId;
      if (!/^https?:/.test(api) || api.includes("YOUR-SUBDOMAIN")) return fallback(it, "offline");
      try {
        // Some outlets block the reader (Reuters always does), so fall through to others covering the story.
        let art = null, shown = it;
        for (const candidate of [it, ...(it._also || [])].slice(0, 4)) {
          const res = await fetch(`${api}/read?url=${encodeURIComponent(candidate.url)}`);
          const got = await res.json();
          if (mine !== reqId) return;
          art = art || got;
          if (got.ok) { art = got; shown = candidate; break; }
        }
        if (!art.ok) return fallback(it, art.reason);
        if (shown !== it) {
          $("#reader-source").textContent = shown.sources[0]?.name || "";
          $("#reader-original").href = safeUrl(shown.url);
        }
        const content = el("div", { className: "reader-content" }, clean(art.content_html));
        const head = header(shown, art, it);
        if (opensWithImage(content)) head.splice(0, head.length, ...head.filter(n => !n.classList.contains("cover")));
        const swapped = shown !== it && el("p", { className: "reader-note", textContent: `${it.sources[0]?.name} blocks the reader, so this is ${shown.sources[0]?.name}'s coverage of the same story.` });
        const partial = art.partial && el("div", { className: "reader-fallback" },
          el("p", { className: "reader-note", textContent: "This is only the excerpt the site publishes in its feed; the full article is behind a bot check." }),
          el("a", { className: "plain-btn", href: safeUrl(it.url), target: "_blank", rel: "noopener noreferrer", textContent: "Read the rest on the original ↗" }));
        body.replaceChildren(...head, ...(swapped ? [swapped] : []), content, ...(partial ? [partial] : []), footer(it) || "");
      } catch {
        if (mine === reqId) fallback(it, "offline");
      }
    }

    function close() {
      if (pane.hidden) return;
      reqId++;
      pane.classList.remove("open");
      document.body.classList.remove("reader-open");
      pane.hidden = true;
      current = null;
    }
    function back() {
      if (pushed) history.back(); // popstate closes it, so the phone's back gesture behaves the same
      else close();
    }
    addEventListener("popstate", () => { pushed = false; close(); });
    $("#reader-back").addEventListener("click", back);
    $("#reader-ask").addEventListener("click", () => current && ask(current, null));
    addEventListener("keydown", e => {
      if (e.key === "Escape" && !pane.hidden && !document.body.classList.contains("companion-open")) back();
    });
    return { open, close };
  })();

  // ---------- Load ----------
  async function load() {
    try {
      const res = await fetch(`data/items.json?t=${Math.floor(Date.now() / 60000)}`);
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      const items = data.items || [];
      state.byId = new Map(items.map(it => [it.id, it]));
      state.updated = data.updated;
      state.leads = P.groupStories(items);
      state.pages = P.build(state.leads);
      if (!state.pages.length) throw new Error("empty feed");
      printPaper();
    } catch (err) {
      console.error(err);
      $("#book").replaceChildren(el("section", { className: "sheet is-front" },
        el("header", { className: "masthead" }, el("h1", { className: "nameplate", textContent: "The AI Radar" })),
        el("p", { className: "stop-press", textContent: "The presses have stopped: this edition couldn't be loaded. Try again shortly." })));
    }
  }

  // ---------- Briefing the companion ----------
  // The paper as text, in printed order, plus any stories matching the question that didn't make the list.
  const BRIEF_MATCHES = 8, BRIEF_PER_SECTION = 3;
  const NOT_A_TOPIC = /^(what|which|when|where|about|today|todays|news|latest|stories|story|top|give|tell|show|there|anything|from|with|this|that|have|been|happen\w*)$/;
  function briefLine(it, n, pageNo) {
    const flag = it.hype ? ", flagged as hype" : it.claim_status === "company-reported" ? ", company-reported" : "";
    const text = storySummary(it).slice(0, 170);
    return `${n}. ${it.title} (${P.outlet(it)}, ${timeAgo(it.published)}, page ${pageNo}${flag})${text ? `: ${text}` : ""}`;
  }
  function brief(question) {
    const words = ((question || "").toLowerCase().match(/[a-z0-9][a-z0-9.+-]{2,}/g) || []).filter(w => !NOT_A_TOPIC.test(w));
    const listed = new Set(), pageOf = new Map(), parts = [`Updated ${timeAgo(state.updated)}.`];
    state.pages.forEach((p, i) => {
      p.stories.forEach(it => pageOf.set(it.id, i + 1));
      const shown = p.kind === "front" ? p.stories : p.stories.slice(0, BRIEF_PER_SECTION);
      shown.forEach(it => listed.add(it.id));
      parts.push(`${p.title.toUpperCase()}\n${shown.map((it, n) => briefLine(it, n + 1, i + 1)).join("\n")}`);
    });
    const hits = state.leads.filter(l => !listed.has(l.id))
      .map(l => [l, words.filter(w => [l, ...l._also].some(m => searchable(m).includes(w))).length])
      .filter(([, n]) => n).sort((a, b) => b[1] - a[1]).slice(0, BRIEF_MATCHES).map(([l]) => l);
    if (hits.length) parts.push(`ALSO MATCHING THE QUESTION\n${hits.map((it, n) => briefLine(it, n + 1, pageOf.get(it.id))).join("\n")}`);
    return parts.join("\n\n");
  }

  window.Radar = { getItem: id => state.byId.get(id), timeAgo, brief };
  load();
})();
