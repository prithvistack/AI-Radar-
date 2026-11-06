// Lays out the newspaper: groups stories about the same event, then assigns each story to exactly one page.
window.RadarPaper = (() => {
  "use strict";

  const FRONT_SIZE = 9;     // lead + eight others
  const FRONT_HOURS = 48;
  const RADAR_SIZE = 7;
  const SECTION_SIZE = 9;
  const SECTIONS = [
    { id: "models", title: "Models & Tools", cats: ["model-release", "tool"], dek: "What shipped, and what it actually does." },
    { id: "research", title: "Research", cats: ["research"], dek: "Papers and findings worth a closer look." },
    { id: "products", title: "Products", cats: ["product"], dek: "What people can use now." },
    { id: "business", title: "Business", cats: ["business"], dek: "Money, deals and the companies behind the models." },
    { id: "policy", title: "Policy & Power", cats: ["policy"], dek: "Rules, governments and who gets to decide." },
  ];

  // ---------- Grouping: different headlines about the same event ----------
  const STOP = new Set(("the a an of to in on for and or with by at from as is are its it his her their this that new " +
    "says said will be has have how why what ai artificial intelligence model models openai google anthropic meta " +
    "nvidia microsoft company").split(" "));
  function tokens(text) {
    const words = (text || "").toLowerCase().normalize("NFKC").replace(/[‐-―]/g, "-")
      .match(/[a-z0-9][a-z0-9.+-]*[a-z0-9]/g) || [];
    return new Set(words.filter(w => w.length > 2 && !STOP.has(w)));
  }

  // Top-tier stories lead their group, then the highest scored; the others hang off lead._also.
  function groupStories(items) {
    const toks = new Map(), df = new Map();
    for (const it of items) {
      const t = tokens(`${it.title} ${it.summary || ""} ${(it.key_terms || []).join(" ")}`);
      toks.set(it.id, t);
      for (const w of t) df.set(w, (df.get(w) || 0) + 1);
    }
    const sameStory = (a, b) => {
      let n = 0, weight = 0, named = false;
      for (const w of toks.get(a.id)) {
        if (!toks.get(b.id).has(w) || df.get(w) > 12) continue;
        n++; weight += 1 / df.get(w);
        named = named || (/\d/.test(w) && /[a-z]/.test(w)); // product names like gpt-6, hy4
      }
      return (n >= 3 && weight >= 0.45) || (named && n >= 2);
    };
    const order = (a, b) => (b.tier === "top") - (a.tier === "top") || b.score - a.score;
    const leads = [];
    for (const it of [...items].sort(order)) {
      const lead = leads.find(l => [l, ...l._also].some(m => sameStory(m, it)));
      if (lead) lead._also.push(it);
      else { it._also = []; leads.push(it); }
    }
    return leads;
  }

  // ---------- Covers ----------
  // A story without a picture of its own (Reuters blocks us) borrows one from another outlet on the same event.
  function coverOf(it) {
    const owner = [it, ...(it._also || [])].find(m => m.image);
    if (!owner) return null;
    const from = owner.sources.find(s => s.url === owner.image_page);
    return { src: owner.image, credit: from && from.name !== "Hacker News" ? from.name : outlet(owner), figure: owner.image.includes("arxiv.org/") };
  }
  // Hacker News only links to the article, so name the site that published it.
  function outlet(it) {
    const name = it.sources[0]?.name || "";
    if (name !== "Hacker News") return name;
    try { return new URL(it.url).hostname.replace(/^www\./, ""); } catch { return name; }
  }
  // The first story with a picture leads, if it is close to the top of the list.
  function leadFirst(list, reach = 3) {
    const i = list.slice(0, reach).findIndex(coverOf);
    return i > 0 ? [list[i], ...list.slice(0, i), ...list.slice(i + 1)] : list;
  }

  // ---------- Page assignment ----------
  function build(leads, now = Date.now()) {
    const used = new Set();
    const take = list => { list.forEach(l => used.add(l.id)); return list; };
    const free = l => !used.has(l.id);
    const good = l => l.substance && !l.hype;

    const cutoff = new Date(now - FRONT_HOURS * 3600e3).toISOString();
    const recent = leads.filter(l => l.published >= cutoff);
    const top = recent.filter(l => l.tier === "top").slice(0, FRONT_SIZE);
    const filler = recent.filter(l => l.tier !== "top" && good(l)).slice(0, FRONT_SIZE - top.length);
    const front = take(leadFirst([...top, ...filler]));

    // Significant, but only one outlet has it.
    const radar = take(leadFirst(leads
      .filter(l => free(l) && good(l) && !l.first_party && !l._also.length && l.sources.length === 1 &&
                   l.significance >= 5 && (l.summary || "").length > 0)
      .sort((a, b) => b.significance - a.significance || b.score - a.score)
      .slice(0, RADAR_SIZE), RADAR_SIZE));

    const pages = [{ kind: "front", id: "front", title: "Front Page", stories: front },
      { kind: "section", id: "radar", title: "Under the Radar", dek: "Stories that matter and that almost nobody else picked up.", stories: radar }];
    for (const s of SECTIONS) {
      const list = leads.filter(l => free(l) && s.cats.includes(l.category) && !l.hype).slice(0, SECTION_SIZE);
      pages.push({ kind: "section", ...s, stories: take(leadFirst(list, list.length)) });
    }
    pages.push({ kind: "other", id: "other", title: "Other Stories", dek: "Everything else from this edition, in brief.", stories: leads.filter(free) });
    return pages.filter(p => p.stories.length);
  }

  return { groupStories, coverOf, outlet, build };
})();
