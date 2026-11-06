// AI Radar companion: floating orb + chat panel backed by the Cloudflare Worker.
// Listens for "radar:context" / "radar:ask" from app.js; emits "radar:context-changed".
(() => {
  "use strict";

  const CFG = window.RADAR_CONFIG || {};
  const API = (CFG.WORKER_URL || "").replace(/\/$/, "");
  const MAX_MESSAGES = 20; // ~10 turns
  // Only sent when it can help: it uses up tokens on every question.
  const NEWS_QUESTION = /\b(top|news|today|latest|headlines?|stor(y|ies)|happen\w*|edition|paper|front page|radar|recent|this week|yesterday|biggest|what'?s new|what else|anything (on|about|from))\b/i;

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* private mode */ } },
  };
  const el = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    n.append(...kids.filter(k => k != null));
    return n;
  };
  const svg = path => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
    s.innerHTML = path; return s;
  };

  const state = {
    context: null,       // {id, title, url, summary, arxiv_id}
    messages: [],        // [{role, content}]
    lastTerm: null,      // term the current question is about (for glossary saves)
    lastQuestion: null,
    busy: false,
    speak: store.get("radar.speak") === "1",
    glossary: [],
  };

  // ---------- DOM ----------
  const orb = el("button", { className: "orb", title: "Ask the companion", ariaLabel: "Open AI companion" });
  const miniOrb = el("span", { className: "mini-orb" });
  const tabChat = el("button", { className: "tab active", textContent: "Chat" });
  const tabGloss = el("button", { className: "tab", textContent: "Learned" });
  const speakBtn = el("button", { className: "icon-btn speak-toggle", title: "Read replies aloud", ariaLabel: "Read replies aloud" },
    svg('<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>'));
  const closeBtn = el("button", { className: "icon-btn", title: "Close", ariaLabel: "Close companion" },
    svg('<path d="M6 6l12 12M18 6 6 18"/>'));
  const ctxText = el("span");
  const ctxClear = el("button", { title: "Clear context", ariaLabel: "Clear context", textContent: "×" });
  const ctxBar = el("div", { className: "context-bar", hidden: true }, ctxText, ctxClear);
  const messagesEl = el("div", { className: "messages" });
  const input = el("textarea", { rows: 1, placeholder: "Ask about today's news…", ariaLabel: "Message" });
  const micBtn = el("button", { className: "icon-btn", title: "Speak", ariaLabel: "Voice input", hidden: true },
    svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'));
  const sendBtn = el("button", { className: "icon-btn send", title: "Send", ariaLabel: "Send" },
    svg('<path d="M5 12h14M13 6l6 6-6 6"/>'));
  const composer = el("div", { className: "composer" }, micBtn, input, sendBtn);
  const glossSearch = el("input", { type: "search", placeholder: "Search what you've learned" });
  const glossList = el("div");
  const glossView = el("div", { className: "glossary", hidden: true }, glossSearch, glossList);
  const chatView = el("div", { style: "display:contents" }, ctxBar, messagesEl, composer);
  const panel = el("aside", { className: "panel", ariaLabel: "AI companion", role: "dialog" },
    el("div", { className: "panel-head" }, miniOrb, el("h2", { textContent: "Companion" }),
      el("div", { className: "tabs" }, tabChat, tabGloss), speakBtn, closeBtn),
    chatView, glossView);
  const scrim = el("div", { className: "scrim", hidden: true });
  const explainPop = el("button", { className: "explain-pop", textContent: "Explain", hidden: true });
  document.body.append(orb, scrim, panel, explainPop);

  speakBtn.setAttribute("aria-pressed", String(state.speak));
  if (!("speechSynthesis" in window)) speakBtn.hidden = true;

  // ---------- Panel ----------
  const isOpen = () => panel.classList.contains("open");
  function openPanel() {
    panel.classList.add("open");
    document.body.classList.add("companion-open");
    scrim.hidden = false;
    if (!messagesEl.children.length) greet();
    setTimeout(() => input.focus({ preventScroll: true }), 50);
  }
  function closePanel() {
    panel.classList.remove("open");
    document.body.classList.remove("companion-open");
    scrim.hidden = true;
  }
  orb.addEventListener("click", openPanel);
  closeBtn.addEventListener("click", closePanel);
  scrim.addEventListener("click", closePanel);
  addEventListener("keydown", e => { if (e.key === "Escape" && isOpen()) closePanel(); });

  function showTab(which) {
    const gloss = which === "gloss";
    tabChat.classList.toggle("active", !gloss);
    tabGloss.classList.toggle("active", gloss);
    chatView.style.display = gloss ? "none" : "contents";
    glossView.hidden = !gloss;
    if (gloss) loadGlossary();
  }
  tabChat.addEventListener("click", () => showTab("chat"));
  tabGloss.addEventListener("click", () => showTab("gloss"));

  function greet() {
    messagesEl.replaceChildren(el("div", { className: "hello" },
      el("h3", { textContent: "Hi! What would you like to know?" }),
      el("p", { textContent: state.context
        ? "I'll use the story you picked as context. Ask anything about it, or ask about today's news."
        : "Ask about today's paper, or highlight any text and hit Explain." }),
      el("div", { className: "msg-actions" }, ...["What are today's top 5 stories?", "What's under the radar today?"].map(q =>
        el("button", { textContent: q, onclick: () => send(q) })))));
  }

  // ---------- Context ----------
  function setContext(item) {
    const next = item ? {
      id: item.id, title: item.title, url: item.url,
      summary: [item.summary, item.raw_summary].filter(Boolean).join("\n\n"),
      arxiv_id: item.extra?.arxiv_id || null,
    } : null;
    if ((next?.id || null) !== (state.context?.id || null)) {
      state.messages = []; // new story, new conversation
      if (isOpen()) greet(); else messagesEl.replaceChildren();
    }
    state.context = next;
    ctxBar.hidden = !next;
    ctxText.textContent = next ? `Reading: ${next.title}` : "";
    ctxText.title = next ? next.title : "";
    orb.classList.toggle("has-context", !!next);
    window.__radarContextId = next?.id || null;
    document.dispatchEvent(new CustomEvent("radar:context-changed"));
  }
  ctxClear.addEventListener("click", () => setContext(null));
  document.addEventListener("radar:context", e => setContext(e.detail.item));
  document.addEventListener("radar:ask", e => {
    const { item, question, term } = e.detail;
    setContext(item);
    openPanel();
    if (question) send(question, { term });
  });

  // ---------- Highlight to explain ----------
  let selectionInfo = null;
  function onSelection() {
    const sel = getSelection();
    const text = sel && !sel.isCollapsed ? sel.toString().trim() : "";
    if (!text || text.length < 2 || text.length > 1500) { explainPop.hidden = true; selectionInfo = null; return; }
    const anchor = sel.anchorNode?.parentElement;
    if (anchor?.closest("textarea, input, .composer, .explain-pop")) return;
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    if (!rect.width && !rect.height) return;
    const card = anchor?.closest(".story");
    selectionInfo = { text, itemId: card?.dataset.id || null };
    explainPop.style.left = `${Math.min(Math.max(rect.left + rect.width / 2, 50), innerWidth - 50) + scrollX}px`;
    explainPop.style.top = `${Math.max(rect.top - 8, 40) + scrollY}px`;
    explainPop.hidden = false;
  }
  let selTimer;
  document.addEventListener("selectionchange", () => { clearTimeout(selTimer); selTimer = setTimeout(onSelection, 250); });
  explainPop.addEventListener("mousedown", e => e.preventDefault()); // keep the selection alive
  explainPop.addEventListener("click", () => {
    if (!selectionInfo) return;
    const { text, itemId } = selectionInfo;
    if (itemId && window.Radar) setContext(window.Radar.getItem(itemId));
    explainPop.hidden = true;
    getSelection()?.removeAllRanges();
    openPanel();
    const short = text.length > 80 ? null : text;
    send(`Explain this${state.context ? " as it's used here" : ""}: "${text}"`, { term: short, selection: text });
  });

  // ---------- Markdown (tiny, safe: escapes first) ----------
  function esc(s) { return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  }
  function md(src) {
    const out = [];
    const blocks = esc(src).split(/```/);
    blocks.forEach((block, i) => {
      if (i % 2) { out.push(`<pre><code>${block.replace(/^\w*\n/, "")}</code></pre>`); return; }
      for (const para of block.split(/\n{2,}/)) {
        const lines = para.split("\n").filter(l => l.trim());
        if (!lines.length) continue;
        if (lines.every(l => /^\s*[-*•]\s+/.test(l))) {
          out.push("<ul>" + lines.map(l => `<li>${inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>`).join("") + "</ul>");
        } else if (lines.every(l => /^\s*\d+[.)]\s+/.test(l))) {
          out.push("<ol>" + lines.map(l => `<li>${inline(l.replace(/^\s*\d+[.)]\s+/, ""))}</li>`).join("") + "</ol>");
        } else {
          out.push(`<p>${lines.map(l => inline(l.replace(/^#{1,4}\s+/, ""))).join("<br>")}</p>`);
        }
      }
    });
    return out.join("");
  }

  // ---------- Chat ----------
  function addUser(text) {
    messagesEl.querySelector(".hello")?.remove();
    messagesEl.append(el("div", { className: "msg user", textContent: text }));
    scrollDown();
  }
  function addAssistant() {
    const bubble = el("div", { className: "bubble" }, el("span", { className: "typing" }, el("i"), el("i"), el("i")));
    const wrap = el("div", { className: "msg assistant" }, bubble);
    messagesEl.append(wrap);
    scrollDown();
    return { wrap, bubble };
  }
  const scrollDown = () => { messagesEl.scrollTop = messagesEl.scrollHeight; };
  function setActivity(cls, on) { orb.classList.toggle(cls, on); miniOrb.classList.toggle(cls, on); }

  async function send(text, opts = {}) {
    text = (text || "").trim();
    if (!text || state.busy) return;
    if (!opts.depth) { state.lastTerm = opts.term || null; state.lastQuestion = text; }
    if (!opts.hidden) addUser(text);
    state.messages.push({ role: "user", content: text });
    state.messages = state.messages.slice(-MAX_MESSAGES);

    state.busy = true; sendBtn.disabled = true; setActivity("thinking", true);
    const { wrap, bubble } = addAssistant();
    let answer = "", model = "";
    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: state.messages,
          paper: !state.context || NEWS_QUESTION.test(text) ? window.Radar?.brief(text) : null,
          depth: opts.depth || null,
          context: state.context ? { ...state.context, selection: opts.selection || null } : (opts.selection ? { selection: opts.selection } : null),
        }),
      });
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `The companion returned HTTP ${res.status}.`);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const evt = buf.slice(0, idx); buf = buf.slice(idx + 2);
          for (const line of evt.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const data = JSON.parse(line.slice(5));
            if (data.delta) { answer += data.delta; bubble.innerHTML = md(answer); scrollDown(); }
            if (data.model) model = data.model;
            if (data.context_note) wrap.dataset.note = data.context_note;
            if (data.error) throw new Error(data.error);
          }
        }
      }
      if (!answer) throw new Error("Empty answer. Try again.");
      state.messages.push({ role: "assistant", content: answer });
      state.messages = state.messages.slice(-MAX_MESSAGES);
      finishAnswer(wrap, answer, model);
      if (state.speak) speak(answer);
    } catch (err) {
      state.messages.pop();
      wrap.classList.add("error");
      bubble.textContent = err.message || "Something went wrong.";
    } finally {
      state.busy = false; sendBtn.disabled = false; setActivity("thinking", false);
    }
  }

  function finishAnswer(wrap, answer, model) {
    const depth = (label, depthKey, prompt) => {
      const b = el("button", { textContent: label });
      b.addEventListener("click", () => { addUser(label); send(prompt, { depth: depthKey, hidden: true }); });
      return b;
    };
    const save = el("button", { textContent: "Save to Learned" });
    save.addEventListener("click", () => saveConcept(save, answer));
    const actions = el("div", { className: "msg-actions" },
      depth("Simpler", "simpler", "Explain that again, simpler."),
      depth("Deeper", "deeper", "Go deeper on that."),
      depth("Show the math", "math", "Show the math behind that."),
      save);
    if ("speechSynthesis" in window) {
      const s = el("button", { textContent: "🔊", title: "Read aloud" });
      s.addEventListener("click", () => speak(answer));
      actions.append(s);
    }
    const meta = [model && `via ${model}`, wrap.dataset.note].filter(Boolean).join(" · ");
    wrap.append(actions);
    if (meta) wrap.append(el("div", { className: "model-tag", textContent: meta }));
    scrollDown();
  }

  sendBtn.addEventListener("click", () => { const t = input.value; input.value = ""; autosize(); send(t); });
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendBtn.click(); }
  });
  const autosize = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 140)}px`; };
  input.addEventListener("input", autosize);

  // ---------- Glossary ----------
  async function api(path, opts = {}) {
    const res = await fetch(`${API}${path}`, {
      ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  async function saveConcept(btn, answer) {
    // One tap: the term that was asked about, else the question itself.
    const term = (state.lastTerm || state.lastQuestion || "Concept")
      .replace(/^(what is|what are|explain( this)?( as it's used here)?:?)\s*/i, "")
      .replace(/^["“]|["”?]+$/g, "").slice(0, 100);
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      await api("/glossary", { method: "POST", body: JSON.stringify({
        term: term.trim(), explanation: answer.slice(0, 2000),
        item_id: state.context?.id || null, url: state.context?.url || null, item_title: state.context?.title || null,
      }) });
      btn.textContent = "Saved ✓"; btn.classList.add("saved");
    } catch (err) {
      btn.disabled = false; btn.textContent = "Save failed, retry";
    }
  }

  async function loadGlossary() {
    glossList.replaceChildren(el("p", { className: "hello", textContent: "Loading…" }));
    try {
      state.glossary = (await api("/glossary")).items || [];
      renderGlossary();
    } catch (err) {
      glossList.replaceChildren(el("p", { className: "hello", textContent: `Couldn't load: ${err.message}` }));
    }
  }
  function renderGlossary() {
    const q = glossSearch.value.trim().toLowerCase();
    const items = state.glossary.filter(g => !q || `${g.term} ${g.explanation}`.toLowerCase().includes(q));
    if (!items.length) {
      glossList.replaceChildren(el("p", { className: "hello", textContent: state.glossary.length
        ? "No matches." : "Nothing saved yet. Tap “Save to Learned” under any answer." }));
      return;
    }
    glossList.replaceChildren(...items.map(g => {
      const del = el("button", { textContent: "Remove" });
      del.addEventListener("click", async () => {
        if (!confirm(`Remove "${g.term}"?`)) return;
        try { await api(`/glossary/${encodeURIComponent(g.id)}`, { method: "DELETE" }); } catch { return; }
        state.glossary = state.glossary.filter(x => x.id !== g.id);
        renderGlossary();
      });
      const foot = el("div", { className: "g-foot" });
      if (g.url) {
        const a = el("a", { textContent: g.item_title || g.url, target: "_blank", rel: "noopener noreferrer" });
        try { const u = new URL(g.url); if (/^https?:$/.test(u.protocol)) a.href = u.href; } catch { /* ignore */ }
        foot.append(a);
      }
      foot.append(del);
      return el("div", { className: "g-item" },
        el("h4", {}, g.term, el("small", { textContent: new Date(g.created).toLocaleDateString() })),
        el("p", { textContent: g.explanation }), foot);
    }));
  }
  glossSearch.addEventListener("input", renderGlossary);

  // ---------- Voice ----------
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SR) {
    micBtn.hidden = false;
    let rec = null;
    micBtn.addEventListener("click", () => {
      if (rec) { rec.stop(); return; }
      rec = new SR();
      rec.lang = navigator.language || "en-US";
      rec.interimResults = true;
      const base = input.value ? input.value + " " : "";
      rec.onresult = e => {
        input.value = base + Array.from(e.results).map(r => r[0].transcript).join("");
        autosize();
      };
      rec.onend = () => {
        micBtn.classList.remove("listening"); rec = null;
        if (input.value.trim()) sendBtn.click();
      };
      rec.onerror = () => { micBtn.classList.remove("listening"); };
      micBtn.classList.add("listening");
      rec.start();
    });
  }
  function speak(text) {
    if (!("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const plain = text.replace(/```[\s\S]*?```/g, " code block ").replace(/[*_`#>]/g, "");
    const u = new SpeechSynthesisUtterance(plain);
    u.lang = navigator.language || "en-US";
    u.onstart = () => setActivity("speaking", true);
    u.onend = u.onerror = () => setActivity("speaking", false);
    speechSynthesis.speak(u);
  }
  speakBtn.addEventListener("click", () => {
    state.speak = !state.speak;
    speakBtn.setAttribute("aria-pressed", String(state.speak));
    store.set("radar.speak", state.speak ? "1" : "0");
    if (!state.speak && "speechSynthesis" in window) speechSynthesis.cancel();
  });
})();
