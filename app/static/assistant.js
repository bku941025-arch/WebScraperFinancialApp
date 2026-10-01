/* FinTrend AI assistant card — embedded in the dashboard. Depends on app.js (App). */
const Assistant = (() => {
  const {esc, api} = App;
  const KEY = 'chat:v1', MAX_HIST = 24;
  const POPULAR = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'JPM'];

  // ---------- small, safe markdown renderer (HTML is escaped first; only http(s) links)
  function inline(s) {
    s = esc(s);
    return s.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\*)/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/(^|[^\w/])\$([A-Z]{1,6}(?:[.-][A-Z]{1,2})?|\d{4,6}\.[A-Z]{1,2})\b/g, '$1<a class="cash" href="/company/$2">$$$2</a>');
  }
  function md(src) {
    const codes = [];
    src = src.replace(/```[\w-]*\n([\s\S]*?)(```|$)/g, (_, c) => { codes.push(`<pre><code>${esc(c.replace(/\n$/, ''))}</code></pre>`); return `\u0000${codes.length - 1}\u0000`; });
    const L = src.split('\n'), out = []; let i = 0;
    const row = j => /^\s*\|.*\|\s*$/.test(L[j] || '');
    const isTable = j => row(j) && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(L[j + 1] || '');
    const cells = r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
    const UL = /^\s*[-*•]\s+/, OL = /^\s*\d+[.)]\s+/;
    while (i < L.length) {
      const l = L[i]; let m;
      if (!l.trim()) { i++; continue; }
      if ((m = l.match(/^\u0000(\d+)\u0000$/))) { out.push(codes[+m[1]]); i++; }
      else if ((m = l.match(/^(#{1,4})\s+(.*)$/))) { out.push(`<h${m[1].length + 2}>${inline(m[2])}</h${m[1].length + 2}>`); i++; }
      else if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { out.push('<hr>'); i++; }
      else if (isTable(i)) { const head = cells(l); i += 2; const rows = []; while (i < L.length && row(i)) rows.push(cells(L[i++]));
        out.push(`<div class="tbl"><table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`); }
      else if (UL.test(l)) { const it = []; while (i < L.length && UL.test(L[i])) it.push(`<li>${inline(L[i++].replace(UL, ''))}</li>`); out.push(`<ul>${it.join('')}</ul>`); }
      else if (OL.test(l)) { const it = []; while (i < L.length && OL.test(L[i])) it.push(`<li>${inline(L[i++].replace(OL, ''))}</li>`); out.push(`<ol>${it.join('')}</ol>`); }
      else if (/^>\s?/.test(l)) { const q = []; while (i < L.length && /^>\s?/.test(L[i])) q.push(inline(L[i++].replace(/^>\s?/, ''))); out.push(`<blockquote>${q.join('<br>')}</blockquote>`); }
      else { const p = []; while (i < L.length && L[i].trim() && !/^(#{1,4}\s|\s*[-*•]\s|\s*\d+[.)]\s|>|\u0000\d+\u0000$)/.test(L[i]) && !isTable(i)) p.push(inline(L[i++])); out.push(`<p>${p.join('<br>') || inline(L[i++] || '')}</p>`); }
    }
    return out.join('');
  }

  const TEMPLATE = `
    <div class="ask-head">
      <div class="orb"><span>✨</span></div>
      <div class="grow"><h2>Ask <span class="grad">FinTrend AI</span></h2><p data-r="sub">Pick a question — I’ll pull live data and answer.</p></div>
      <span class="tag" data-r="badge" hidden></span>
      <button class="mini-btn" data-r="clear" hidden>Clear chat</button>
      <button class="icon-btn" data-r="toggle" aria-expanded="true" aria-label="Collapse assistant"><span class="chev">⌄</span></button>
    </div>
    <div class="ask-peek" data-r="peek"></div>
    <div class="ask-body">
      <div class="banner" data-r="banner" hidden></div>
      <div class="ask-grid">
        <aside class="bank">
          <h3 class="bank-title">Question bank</h3>
          <div class="btabs" data-r="tabs" role="tablist"></div>
          <div class="blist" data-r="list"></div>
          <div class="bank-foot" data-r="foot"></div>
        </aside>
        <section class="pane">
          <button class="stop" data-r="stop" hidden>■ Stop</button>
          <div class="pane-empty" data-r="empty"></div>
          <div class="thread" data-r="thread" aria-live="polite" hidden></div>
          <form class="composer" data-r="form" hidden>
            <textarea data-r="box" rows="1" maxlength="2000" placeholder="…or type your own question" aria-label="Message"></textarea>
            <button class="webtog" data-r="web" type="button" hidden aria-pressed="false" title="Let the assistant search the web for this question (uses extra API credits)">🌐 Web</button>
            <button class="send" data-r="send" type="submit" aria-label="Send" disabled>➤</button>
          </form>
        </section>
      </div>
    </div>`;

  async function mount(root) {
    root.classList.add('ask'); root.innerHTML = TEMPLATE;
    const R = {}; root.querySelectorAll('[data-r]').forEach(e => R[e.dataset.r] = e);
    const st = {msgs: [], busy: false, ctrl: null, status: {provider: 'rules', model: 'Built-in assistant'}, web: false, bank: [], tab: 'pulse',
                tickers: [], slots: {}, other: {}, open: true};
    try { st.msgs = (JSON.parse(localStorage.getItem(KEY) || '[]') || []).filter(m => m && m.role && typeof m.content === 'string'); } catch {}
    try { st.web = localStorage.getItem('chat:web') === '1'; st.open = localStorage.getItem('ask:open') !== '0'; } catch {}
    const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st.msgs.slice(-40).map(m => ({role: m.role, content: m.content, tools: m.tools, sources: m.sources})))); } catch {} };

    // ---------- conversation
    const nearBottom = () => R.thread.scrollHeight - R.thread.scrollTop - R.thread.clientHeight < 160;
    const toBottom = () => { R.thread.scrollTop = R.thread.scrollHeight; };
    function view(i) {
      const m = st.msgs[i]; let el = R.thread.querySelector('#m' + i);
      if (!el) { el = document.createElement('div'); el.id = 'm' + i; el.className = 'msg ' + (m.role === 'user' ? 'user' : 'ai'); R.thread.appendChild(el); }
      if (m.role === 'user') { el.innerHTML = `<div class="bub">${esc(m.content)}</div>`; return; }
      const last = i === st.msgs.length - 1, live = last && st.busy;
      const groups = new Map();
      (m.tools || []).forEach(t => { const g = groups.get(t.label) || {n: 0, done: 0, bad: 0}; g.n++; if (t.done) { g.done++; if (!t.ok) g.bad++; } groups.set(t.label, g); });
      const chips = [...groups].map(([label, g]) => { const fin = g.done === g.n;
        return `<span class="tool-chip ${fin ? (g.bad ? 'bad' : 'ok') : ''}">${fin ? (g.bad ? '!' : '✓') : '<i class="spin"></i>'} ${esc(label)}${g.n > 1 ? ' ×' + g.n : ''}</span>`; }).join('');
      el.innerHTML = `<div class="orb ${live ? 'think' : ''}"><span>✨</span></div><div class="bub">
        ${chips ? `<div class="tools">${chips}</div>` : ''}
        ${m.content ? `<div class="md ${live ? 'caret' : ''}">${md(m.content)}</div>` : (live && !chips ? '<div class="typing"><i></i><i></i><i></i></div>' : '')}
        ${m.sources && m.sources.length ? `<div class="sources"><span class="lbl">${m.sources[0].cited ? 'Sources' : 'Searched'}</span>${m.sources.map(x => `<a class="source-pill" href="${App.safeUrl(x.url)}" target="_blank" rel="noopener noreferrer" title="${esc(x.title)}"><i style="background:hsl(${App.hue(x.site)} 60% 45%)">${esc((x.site[0] || '?').toUpperCase())}</i><span>${esc(x.site)}</span></a>`).join('')}</div>` : ''}
        ${m.notice ? `<div class="note">${esc(m.notice)}</div>` : ''}
        ${m.err ? `<div class="err">⚠ ${esc(m.err)} <button class="chip sm" data-act="retry">Try again</button></div>` : ''}
        ${!live && m.content ? `<div class="msg-actions ${last ? 'show' : ''}"><button class="mini-btn" data-act="copy" data-i="${i}">⧉ Copy</button>${last ? '<button class="mini-btn" data-act="regen">↻ Regenerate</button>' : ''}</div>` : ''}</div>`;
    }
    function renderThread() {
      R.thread.innerHTML = ''; st.msgs.forEach((_, i) => view(i));
      const has = st.msgs.length > 0; R.thread.hidden = !has; R.empty.hidden = has; R.clear.hidden = !has; toBottom();
    }
    let raf = 0;
    const paint = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; const nb = nearBottom(); view(st.msgs.length - 1); if (nb) toBottom(); }); };
    function setBusy(b) {
      st.busy = b; R.stop.hidden = !b; R.send.disabled = b || !R.box.value.trim();
      root.querySelectorAll('.qrow').forEach(r => r.style.pointerEvents = b ? 'none' : ''); root.querySelectorAll('.qrow').forEach(r => r.style.opacity = b ? .55 : '');
    }

    async function ask(text, {regen = false} = {}) {
      if (st.busy || !text.trim()) return;
      if (!st.open) setOpen(true);
      if (!regen) st.msgs.push({role: 'user', content: text.trim()});
      st.msgs.push({role: 'assistant', content: '', tools: []});
      renderThread(); setBusy(true);
      if (matchMedia('(max-width:900px)').matches) R.empty.closest('.pane').scrollIntoView({behavior: 'smooth', block: 'start'});   // stacked layout: bring the answer into view
      else if (root.getBoundingClientRect().top < 0 || root.getBoundingClientRect().bottom > innerHeight) root.scrollIntoView({behavior: 'smooth', block: 'nearest'});
      const hist = st.msgs.slice(0, -1).filter(m => m.content && !m.err).map(m => ({role: m.role, content: m.content.slice(0, 5900)})).slice(-MAX_HIST);
      const ai = st.msgs[st.msgs.length - 1]; st.ctrl = new AbortController();
      try {
        const res = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({messages: hist, web: st.web}), signal: st.ctrl.signal});
        if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(typeof j.detail === 'string' ? j.detail : 'Request failed (' + res.status + ')'); }
        const rd = res.body.getReader(), dec = new TextDecoder(); let buf = '';
        for (;;) {
          const {value, done} = await rd.read(); if (done) break;
          buf += dec.decode(value, {stream: true}); let k;
          while ((k = buf.indexOf('\n\n')) >= 0) { const chunk = buf.slice(0, k); buf = buf.slice(k + 2); if (!chunk.startsWith('data: ')) continue;
            const e = JSON.parse(chunk.slice(6));
            if (e.type === 'delta') ai.content += e.text;
            else if (e.type === 'tool_start') ai.tools.push({id: e.id, label: e.label, done: false});
            else if (e.type === 'tool_done') { const t = ai.tools.find(t => t.id === e.id); if (t) { t.done = true; t.ok = e.ok; } }
            else if (e.type === 'notice') ai.notice = e.text;
            else if (e.type === 'sources') ai.sources = e.items;
            else if (e.type === 'error') ai.err = e.message;
            paint(); }
        }
      } catch (err) { if (err.name === 'AbortError') ai.notice = 'Stopped.'; else ai.err = err.message || 'Something went wrong.'; }
      if (!ai.content && !ai.err && !ai.notice) ai.err = 'No answer came back.';
      st.ctrl = null; setBusy(false); view(st.msgs.length - 1); save();
    }

    // ---------- question bank
    const tick = k => st.slots[k];
    function slotHTML(k) {
      if (st.other[k]) return `<input class="slot-in" data-slot="${k}" placeholder="ticker or name" aria-label="Company ${k}" value="${esc(st.other[k] === true ? '' : st.other[k])}" autocomplete="off">`;
      const trending = st.tickers.map(t => `<option value="${esc(t.ticker)}" ${tick(k) === t.ticker ? 'selected' : ''}>${esc(t.ticker)} · ${esc(t.name.slice(0, 18))}</option>`).join('');
      const have = new Set(st.tickers.map(t => t.ticker));
      const pop = POPULAR.filter(t => !have.has(t)).map(t => `<option value="${t}" ${tick(k) === t ? 'selected' : ''}>${t}</option>`).join('');
      return `<select class="slot" data-slot="${k}" aria-label="Company ${k}">${trending ? `<optgroup label="Trending now">${trending}</optgroup>` : ''}<optgroup label="Popular">${pop}</optgroup><option value="__other">Other…</option></select>`;
    }
    function renderBank() {
      R.tabs.innerHTML = st.bank.map(c => `<button class="btab ${c.id === st.tab ? 'on' : ''}" data-tab="${c.id}" role="tab" aria-selected="${c.id === st.tab}">${c.icon} ${esc(c.title)}</button>`).join('');
      const cat = st.bank.find(c => c.id === st.tab) || st.bank[0]; if (!cat) return;
      R.list.innerHTML = cat.questions.map((q, i) => {
        const parts = q.text.split(/(\{[AB]\})/).map(p => /^\{[AB]\}$/.test(p) ? slotHTML(p[1]) : esc(p)).join('');
        return `<div class="qrow" style="--i:${i}" role="button" tabindex="0" data-q="${i}"><span class="qt">${parts}</span><button class="go" type="button" tabindex="-1" aria-label="Ask">➤</button></div>`;
      }).join('');
    }
    async function resolveSlot(k) {
      if (!st.other[k]) return tick(k);
      const v = typeof st.other[k] === 'string' ? st.other[k].trim() : ''; if (!v) return null;
      const r = await api('/api/search?q=' + encodeURIComponent(v)).catch(() => []);
      const real = r.filter(x => !x.raw);                                       // genuine matches first
      const exact = real.find(x => x.ticker.toUpperCase() === v.toUpperCase());  // "AAPL"
      if (exact || real.length) return (exact || real[0]).ticker;               // "Tesla" -> TSLA
      const sym = r.find(x => x.raw && x.ticker.toUpperCase() === v.toUpperCase());
      return sym ? sym.ticker : null;                                           // unlisted symbol such as 0700.HK
    }
    async function askBank(qi) {
      const q = (st.bank.find(c => c.id === st.tab) || {questions: []}).questions[qi]; if (!q) return;
      if (st.busy) { App.toast('Still answering — one moment…'); return; }
      const vals = {};
      for (const k of q.slots) { vals[k] = await resolveSlot(k); if (!vals[k]) { App.toast('Pick a company for the question'); return; } }
      if (q.slots.length === 2 && vals.A === vals.B) { App.toast('Pick two different companies to compare'); return; }
      ask(q.text.replace(/\{([AB])\}/g, (_, k) => '$' + vals[k]));
    }

    function setOpen(o) {
      st.open = o; root.classList.toggle('closed', !o); R.toggle.setAttribute('aria-expanded', o); R.toggle.setAttribute('aria-label', o ? 'Collapse assistant' : 'Expand assistant');
      try { localStorage.setItem('ask:open', o ? '1' : '0'); } catch {}
    }

    // ---------- events
    R.toggle.onclick = () => setOpen(!st.open);
    root.querySelector('.ask-head').addEventListener('click', e => { if (!st.open && !e.target.closest('button')) setOpen(true); });
    R.tabs.onclick = e => { const b = e.target.closest('[data-tab]'); if (b) { st.tab = b.dataset.tab; renderBank(); } };
    R.list.addEventListener('change', e => {
      const sel = e.target.closest('select.slot'); if (!sel) return; const k = sel.dataset.slot;
      if (sel.value === '__other') { st.other[k] = true; renderBank(); sel.closest('.qrow') && R.list.querySelector(`.qrow[data-q="${sel.closest('.qrow').dataset.q}"] .slot-in`)?.focus(); }
      else { st.slots[k] = sel.value; R.list.querySelectorAll(`select.slot[data-slot="${k}"]`).forEach(x => x.value = sel.value); }   // keep every row's picker in sync
    });
    R.list.addEventListener('input', e => { const inp = e.target.closest('input.slot-in'); if (!inp) return; const k = inp.dataset.slot;
      st.other[k] = inp.value || true; R.list.querySelectorAll(`input.slot-in[data-slot="${k}"]`).forEach(x => { if (x !== inp) x.value = inp.value; }); });
    R.list.addEventListener('click', e => { if (e.target.closest('select, input')) return; const r = e.target.closest('.qrow'); if (r) askBank(+r.dataset.q); });
    R.list.addEventListener('keydown', e => {
      if (e.target.matches('.slot-in') && e.key === 'Enter') { e.preventDefault(); askBank(+e.target.closest('.qrow').dataset.q); }
      else if (e.target.matches('.qrow') && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); askBank(+e.target.dataset.q); }
    });
    R.peek.onclick = e => { const b = e.target.closest('[data-pq]'); if (b) { setOpen(true); ask(b.dataset.pq); } };
    R.stop.onclick = () => st.ctrl?.abort();
    R.clear.onclick = () => { st.ctrl?.abort(); st.msgs = []; save(); setBusy(false); renderThread(); };
    root.addEventListener('click', e => {
      const a = e.target.closest('[data-act]'); if (!a) return;
      if (a.dataset.act === 'copy') { navigator.clipboard?.writeText(st.msgs[+a.dataset.i].content); a.textContent = '✓ Copied'; setTimeout(() => a.textContent = '⧉ Copy', 1400); }
      if (a.dataset.act === 'regen' || a.dataset.act === 'retry') { st.msgs.pop(); const u = st.msgs[st.msgs.length - 1]; if (u?.role === 'user') ask(u.content, {regen: true}); }
    });
    // free-text composer (only shown for local-model / Claude engines)
    const grow = () => { R.box.style.height = 'auto'; R.box.style.height = Math.min(R.box.scrollHeight, 130) + 'px'; R.send.disabled = st.busy || !R.box.value.trim(); };
    R.box.oninput = grow;
    R.box.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); R.form.requestSubmit(); } };
    R.form.onsubmit = e => { e.preventDefault(); const t = R.box.value; if (!t.trim() || st.busy) return; R.box.value = ''; grow(); ask(t); };

    // ---------- initial render, then load config + data
    R.empty.innerHTML = `<div class="orb big"><span>✨</span></div><h3>Choose a question</h3><p>Pick one from the question bank — I’ll pull live trends, prices, news, filings and earnings to answer.</p>
      <div class="uses"><span class="tag">📈 Trends</span><span class="tag">💹 Prices &amp; technicals</span><span class="tag">📅 Earnings</span><span class="tag">🏛 SEC filings</span></div>`;
    setOpen(st.open); renderThread(); R.list.innerHTML = '<div class="skel" style="height:56px"></div><div class="skel" style="height:56px"></div>';
    const [bank, status, trending] = await Promise.all([api('/api/chat/questions').catch(() => []), api('/api/chat/status').catch(() => st.status), api('/api/trends?hours=24&limit=10').catch(() => [])]);
    st.bank = bank; st.status = status; st.tickers = trending.map(t => ({ticker: t.ticker, name: t.name}));
    st.slots = {A: (st.tickers[0] || {ticker: 'AAPL'}).ticker, B: (st.tickers[1] || {ticker: 'MSFT'}).ticker};
    renderBank();
    R.peek.innerHTML = '<span class="src" style="align-self:center">Try:</span>' + bank.flatMap(c => c.questions).filter(q => q.featured).map(q => `<button class="chip" data-pq="${esc(q.text)}">${esc(q.text)}</button>`).join('');

    const p = status.provider, free = p === 'ollama' || p === 'claude';
    R.badge.hidden = false;
    R.badge.textContent = {rules: '🧩 Built-in · no AI', ollama: '🦙 ' + status.model + ' · local', claude: '⚡ ' + status.model}[p] || status.model;
    R.badge.title = {rules: 'Answers come from rules over your own data — no AI model, no cost', ollama: 'Running on your machine via Ollama — no API cost', claude: 'Claude API — uses API credits'}[p] || '';
    if (status.reason) { R.banner.hidden = false; R.banner.textContent = 'ℹ ' + status.reason; }
    R.form.hidden = !free;
    if (free) R.sub.textContent = 'Pick a question from the bank, or type your own.';
    if (status.web_search) { R.web.hidden = false; const paint = () => { R.web.classList.toggle('on', st.web); R.web.setAttribute('aria-pressed', st.web); }; paint();
      R.web.onclick = () => { st.web = !st.web; try { localStorage.setItem('chat:web', st.web ? '1' : '0'); } catch {} paint(); App.toast(st.web ? '🌐 Web search on for your next questions' : 'Web search off'); }; }
    R.foot.innerHTML = p === 'rules' ? `🧩 <b>Built-in assistant</b> — instant, free, no AI.
      <details><summary>Want free-form questions?</summary><p>Run a local model: install <a href="https://ollama.com" target="_blank" rel="noopener" style="color:var(--a1);font-weight:700">Ollama</a>, then <code>ollama pull llama3.1</code> and start the server with <code>FINTREND_CHAT=ollama</code>.</p>
      <p>Or use Claude: <code>ANTHROPIC_API_KEY=…</code> <code>FINTREND_CHAT=claude</code>.</p></details>` : '';
    grow();
    return {ask};
  }
  return {mount};
})();
