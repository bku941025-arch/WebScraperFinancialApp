/* FinTrend AI assistant card — embedded in the dashboard. Depends on app.js (App).
   Guided flow: home (search a company / pick a topic) → topic questions or company actions → conversation. */
const Assistant = (() => {
  const {esc, api} = App;
  const KEY = 'chat:v1', MAX_HIST = 24;

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
      <div class="grow"><h2>Ask <span class="grad">FinTrend AI</span></h2><p data-r="sub">Ask about any company or the market — answered from live data.</p></div>
      <span class="tag" data-r="badge" hidden></span>
      <button class="mini-btn" data-r="clear" hidden>Clear chat</button>
      <button class="icon-btn" data-r="toggle" aria-expanded="true" aria-label="Collapse assistant"><span class="chev">⌄</span></button>
    </div>
    <div class="ask-peek" data-r="peek"></div>
    <div class="ask-body">
      <div class="banner" data-r="banner" hidden></div>
      <div class="ask-main" data-r="main"><div class="skel" style="height:56px;border-radius:999px"></div><div class="skel" style="height:110px;margin-top:14px"></div></div>
      <form class="composer" data-r="form" hidden>
        <div class="box"><textarea data-r="box" rows="1" maxlength="2000" placeholder="…or type your own question" aria-label="Type your own question"></textarea>
          <button class="webtog" data-r="web" type="button" hidden aria-pressed="false" title="Let the assistant search the web for this question (uses extra API credits)">🌐 Web</button>
          <button class="send" data-r="send" type="submit" aria-label="Send" disabled>➤</button></div>
      </form>
    </div>`;

  async function mount(root) {
    root.classList.add('ask'); root.innerHTML = TEMPLATE;
    const R = {}; root.querySelectorAll('[data-r]').forEach(e => R[e.dataset.r] = e);
    const st = {msgs: [], busy: false, ctrl: null, status: {provider: 'rules', model: 'Built-in assistant'}, web: false, bank: [], tickers: [],
                view: 'home', topic: null, company: null, compare: false, ctx: null, open: true, pk: {items: [], act: 0, tok: 0}};
    try { st.msgs = (JSON.parse(localStorage.getItem(KEY) || '[]') || []).filter(m => m && m.role && typeof m.content === 'string'); } catch {}
    try { st.web = localStorage.getItem('chat:web') === '1'; st.open = localStorage.getItem('ask:open') !== '0'; } catch {}
    const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st.msgs.slice(-40).map(m => ({role: m.role, content: m.content, tools: m.tools, sources: m.sources})))); } catch {} };
    const cat = id => st.bank.find(c => c.id === id);
    const ctxAttr = o => `data-ctx="${esc(JSON.stringify(o))}"`;
    const thread = () => R.main.querySelector('[data-t=thread]');

    // ---------- conversation messages
    const nearBottom = () => { const t = thread(); return !t || t.scrollHeight - t.scrollTop - t.clientHeight < 160; };
    const toBottom = () => { const t = thread(); if (t) t.scrollTop = t.scrollHeight; };
    function view(i) {
      const T = thread(); if (!T) return; const m = st.msgs[i]; let el = T.querySelector('#m' + i);
      if (!el) { el = document.createElement('div'); el.id = 'm' + i; el.className = 'msg ' + (m.role === 'user' ? 'user' : 'ai'); T.appendChild(el); }
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
    let raf = 0;
    const paint = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; const nb = nearBottom(); view(st.msgs.length - 1); if (nb && st.busy) toBottom(); }); };   // follow the text only while it streams

    // ---------- follow-up suggestions (client-side, from what the user just asked)
    function suggestions() {
      const c = st.ctx; if (!c) return [];
      if (c.kind === 'company') return cat('company').questions.filter(q => q.text !== c.text).map(q => q.slots.length > 1
        ? {label: q.icon + ' Compare…', go: 'compare'}
        : {label: q.icon + ' ' + q.label, text: q.text.replace('{A}', '$' + c.ticker), ctx: {kind: 'company', ticker: c.ticker, name: c.name, text: q.text}}).slice(0, 5);
      const out = (cat(c.id)?.questions || []).filter(q => q.text !== c.text && !q.slots.length).slice(0, 3).map(q => ({label: q.text, text: q.text, ctx: {kind: 'topic', id: c.id, text: q.text}}));
      const top = st.tickers[0];
      if ((c.id === 'pulse' || c.id === 'calendar') && top) out.push({label: '📊 Full read on ' + top.ticker, text: 'Give me a full read on $' + top.ticker, ctx: {kind: 'company', ticker: top.ticker, name: top.name, text: cat('company').questions[0].text}});
      return out;
    }
    function dockHTML() {
      if (st.busy) return '<span class="grow"></span><button class="stopbtn" data-stop>■ Stop</button>';
      const sug = suggestions();
      return (sug.length ? '<span class="sec-lbl">Ask next</span>' : '') + sug.map(s => s.go ? `<button class="pop" data-go="${s.go}">${esc(s.label)}</button>`
        : `<button class="pop" data-ask="${esc(s.text)}" ${ctxAttr(s.ctx)}>${esc(s.label)}</button>`).join('') + '<span class="grow"></span><button class="nq" data-go="home">＋ New question</button>';
    }
    function setBusy(b) {
      st.busy = b; R.send.disabled = b || !R.box.value.trim();
      const d = R.main.querySelector('[data-t=dock]'); if (d) d.innerHTML = dockHTML();
    }

    // ---------- views
    const tchip = (t, attr) => `<button class="tchip" ${attr}="${esc(t.ticker)}" data-name="${esc(t.name)}" title="${esc(t.name)}"><i class="${t.tone > .15 ? 'up' : t.tone < -.15 ? 'down' : ''}"></i>${esc(t.ticker)}</button>`;
    const pickerHTML = (id, ph, small) => `<div class="cp ${small ? 'sm' : ''}" data-picker="${id}"><div class="cp-box"><span aria-hidden="true">🔍</span>
      <input class="cp-input" placeholder="${esc(ph)}" autocomplete="off" spellcheck="false" aria-label="${esc(ph)}"></div><div class="cp-drop" hidden></div></div>`;
    function upgradeHTML() {
      if (st.status.provider !== 'rules') return '';
      return `<details class="upgrade"><summary>Want to type your own questions?</summary>
        <p>The built-in assistant is instant and free but answers the questions above. For free-form questions, run a local model: install <a href="https://ollama.com" target="_blank" rel="noopener">Ollama</a>, <code>ollama pull llama3.1</code>, then start the server with <code>FINTREND_CHAT=ollama</code>. Or use Claude: <code>ANTHROPIC_API_KEY=…</code> with <code>FINTREND_CHAT=claude</code>.</p></details>`;
    }
    function homeHTML() {
      const topics = st.bank.filter(c => c.id !== 'company'), featured = st.bank.flatMap(c => c.questions.filter(q => q.featured).map(q => ({q, c})));
      return `<h3 class="ask-q">What would you like to know?</h3>
        ${pickerHTML('home', 'Look up a company — try NVDA, Tesla or 7203.T', false)}
        ${st.tickers.length ? `<div class="trend-row"><span class="lbl">Trending now</span>${st.tickers.slice(0, 6).map(t => tchip(t, 'data-company')).join('')}</div>` : ''}
        <div class="topics">${topics.map((c, i) => `<button class="topic" style="--i:${i}" data-topic="${c.id}"><span class="ic">${c.icon}</span><b>${esc(c.title)}</b><small>${esc(c.blurb)}</small></button>`).join('')}</div>
        <div class="popular"><span class="sec-lbl">Popular</span>${featured.map(({q, c}) => `<button class="pop" data-ask="${esc(q.text)}" ${ctxAttr({kind: 'topic', id: c.id, text: q.text})}>${esc(q.text)}</button>`).join('')}</div>
        ${st.msgs.length ? '<div class="resume"><button class="back" data-go="chat">↩ Back to your conversation</button></div>' : ''}${upgradeHTML()}`;
    }
    function topicHTML() {
      const c = cat(st.topic);
      return `<div class="ask-nav"><button class="back" data-go="home">← Back</button><span class="ttl">${c.icon} ${esc(c.title)}</span></div>
        <div class="qlist">${c.questions.map((q, i) => `<button class="qbtn" style="--i:${i}" data-ask="${esc(q.text)}" ${ctxAttr({kind: 'topic', id: c.id, text: q.text})}>${esc(q.text)}</button>`).join('')}</div>`;
    }
    function companyHTML() {
      const co = st.company, acts = cat('company').questions;
      return `<div class="ask-nav"><button class="back" data-go="home">← Back</button>
          <span class="co">${esc(co.ticker)} <small>${esc(co.name)}</small><button data-go="home" aria-label="Choose a different company" title="Choose a different company">✕</button></span></div>
        <h3 class="ask-q" style="font-size:22px;margin-top:12px">What do you want to know about ${esc(co.ticker)}?</h3>
        <div class="tiles">${acts.map((q, i) => q.slots.length > 1
          ? `<button class="tile ${st.compare ? 'on' : ''}" style="--i:${i}" data-compare-toggle><span class="ic">${q.icon}</span><b>${esc(q.label)}</b><small>${esc(q.hint)}</small></button>`
          : `<button class="tile" style="--i:${i}" data-ask="${esc(q.text.replace('{A}', '$' + co.ticker))}" ${ctxAttr({kind: 'company', ticker: co.ticker, name: co.name, text: q.text})}><span class="ic">${q.icon}</span><b>${esc(q.label)}</b><small>${esc(q.hint)}</small></button>`).join('')}</div>
        ${st.compare ? `<div class="cmp"><p>Compare ${esc(co.ticker)} with…</p>${pickerHTML('compare', 'Search a company to compare', true)}
          <div class="trend-row">${st.tickers.filter(t => t.ticker !== co.ticker).slice(0, 6).map(t => tchip(t, 'data-cmp')).join('')}</div></div>` : ''}`;
    }
    function render() {
      const v = st.view;
      R.main.innerHTML = `<div class="ask-view">${v === 'topic' ? topicHTML() : v === 'company' ? companyHTML() : v === 'chat' ? '<div class="thread" data-t="thread" aria-live="polite"></div><div class="dock" data-t="dock"></div>' : homeHTML()}</div>`;
      if (v === 'chat') { st.msgs.forEach((_, i) => view(i)); toBottom(); R.main.querySelector('[data-t=dock]').innerHTML = dockHTML(); }
      R.clear.hidden = !st.msgs.length;
    }
    const go = (v, o = {}) => { Object.assign(st, {view: v}, o); render(); };

    // ---------- asking
    async function ask(text, ctx = null, {regen = false} = {}) {
      if (st.busy || !text.trim()) return;
      if (!st.open) setOpen(true);
      const inChat = st.view === 'chat' && thread() && !regen;               // follow-up inside the conversation: append, don't rebuild
      st.ctx = ctx; if (!regen) st.msgs.push({role: 'user', content: text.trim()});
      st.msgs.push({role: 'assistant', content: '', tools: []});
      st.view = 'chat';
      if (inChat) { view(st.msgs.length - 2); view(st.msgs.length - 1); setBusy(true); toBottom(); R.clear.hidden = false; }
      else { st.busy = true; render(); }
      R.send.disabled = true;
      if (root.getBoundingClientRect().top < 0 || root.getBoundingClientRect().bottom > innerHeight) root.scrollIntoView({behavior: 'smooth', block: 'nearest'});
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
      st.ctrl = null; setBusy(false); view(st.msgs.length - 1); R.clear.hidden = false; save();
      const T = thread(), u = T && T.querySelector('#m' + (st.msgs.length - 2));
      if (T && u && T.scrollHeight > T.clientHeight) T.scrollTo({top: Math.max(0, u.offsetTop - 6), behavior: 'smooth'});   // start reading from the question, not the end
    }
    function setOpen(o) {
      st.open = o; root.classList.toggle('closed', !o); R.toggle.setAttribute('aria-expanded', o); R.toggle.setAttribute('aria-label', o ? 'Collapse assistant' : 'Expand assistant');
      try { localStorage.setItem('ask:open', o ? '1' : '0'); } catch {}
    }

    // ---------- company search box (autocomplete)
    const hideDrops = () => R.main.querySelectorAll('.cp-drop').forEach(d => d.hidden = true);
    async function showDrop(input) {
      const drop = input.closest('.cp').querySelector('.cp-drop'), q = input.value.trim(), tok = ++st.pk.tok; let items, head = '';
      if (!q) { items = st.tickers.slice(0, 8).map(t => ({ticker: t.ticker, name: t.name})); head = 'Trending now'; }
      else { const r = await api('/api/search?q=' + encodeURIComponent(q)).catch(() => []); if (tok !== st.pk.tok) return; const real = r.filter(x => !x.raw); items = real.length ? real : r; }
      st.pk.items = items; st.pk.act = 0; st.pk.picker = input.closest('.cp').dataset.picker;
      drop.hidden = false;
      drop.innerHTML = items.length ? (head ? `<h4>${head}</h4>` : '') + items.map((t, i) => `<button type="button" class="cp-item ${i === 0 ? 'act' : ''}" data-pick="${i}"><b>${esc(t.ticker)}</b><span>${esc(t.name)}</span></button>`).join('')
        : '<div class="cp-empty">No match — try a ticker like AAPL or 0700.HK</div>';
    }
    function pick(item) {
      const picker = st.pk.picker; const co = {ticker: item.ticker, name: item.raw ? item.ticker : item.name};
      if (picker === 'compare') return askCompare(co);
      st.company = co; st.compare = false; go('company');
    }
    function askCompare(b) {
      const a = st.company; if (b.ticker === a.ticker) { App.toast('Pick a different company to compare'); return; }
      ask(`Compare $${a.ticker} with $${b.ticker}`, {kind: 'company', ticker: a.ticker, name: a.name, text: cat('company').questions.find(q => q.slots.length > 1).text});
    }
    let dt;
    R.main.addEventListener('input', e => { if (!e.target.matches('.cp-input')) return; clearTimeout(dt); dt = setTimeout(() => showDrop(e.target), 150); });
    R.main.addEventListener('focusin', e => { if (e.target.matches('.cp-input') && !e.target.value) showDrop(e.target); });
    R.main.addEventListener('keydown', e => {
      if (!e.target.matches('.cp-input')) return; const drop = e.target.closest('.cp').querySelector('.cp-drop'), n = st.pk.items.length;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { if (drop.hidden) { showDrop(e.target); return; } e.preventDefault(); if (!n) return;
        st.pk.act = (st.pk.act + (e.key === 'ArrowDown' ? 1 : n - 1)) % n; drop.querySelectorAll('.cp-item').forEach((b, i) => b.classList.toggle('act', i === st.pk.act)); drop.querySelector('.act')?.scrollIntoView({block: 'nearest'}); }
      else if (e.key === 'Enter') { e.preventDefault(); const v = e.target.value.trim(); st.pk.picker = e.target.closest('.cp').dataset.picker;
        if (!drop.hidden && n && st.pk.items[st.pk.act]) pick(st.pk.items[st.pk.act]);
        else if (v) showDrop(e.target).then(() => st.pk.items[0] && pick(st.pk.items[0])); }
      else if (e.key === 'Escape') hideDrops();
    });
    document.addEventListener('click', e => { if (!e.target.closest('.cp')) hideDrops(); });

    // ---------- clicks
    R.main.addEventListener('click', e => {
      const t = e.target, $ = s => t.closest(s);
      let el;
      if ((el = $('[data-pick]'))) { st.pk.picker = el.closest('.cp').dataset.picker; pick(st.pk.items[+el.dataset.pick]); }
      else if ((el = $('[data-go]'))) { const g = el.dataset.go; if (g === 'compare') go('company', {compare: true, company: st.ctx?.ticker ? {ticker: st.ctx.ticker, name: st.ctx.name} : st.company}); else if (g === 'chat') go('chat'); else go('home', {compare: false}); }
      else if ((el = $('[data-company]'))) { st.company = {ticker: el.dataset.company, name: el.dataset.name}; st.compare = false; go('company'); }
      else if ((el = $('[data-cmp]'))) askCompare({ticker: el.dataset.cmp, name: el.dataset.name});
      else if ((el = $('[data-compare-toggle]'))) go('company', {compare: !st.compare});
      else if ((el = $('[data-topic]'))) go('topic', {topic: el.dataset.topic});
      else if ((el = $('[data-stop]'))) st.ctrl?.abort();
      else if ((el = $('[data-ask]'))) { let ctx = null; try { ctx = JSON.parse(el.dataset.ctx || 'null'); } catch {} ask(el.dataset.ask, ctx); }
      else if ((el = $('[data-act]'))) {
        if (el.dataset.act === 'copy') { navigator.clipboard?.writeText(st.msgs[+el.dataset.i].content); el.textContent = '✓ Copied'; setTimeout(() => el.textContent = '⧉ Copy', 1400); }
        else { st.msgs.pop(); const u = st.msgs[st.msgs.length - 1]; if (u?.role === 'user') ask(u.content, st.ctx, {regen: true}); }
      }
    });
    R.toggle.onclick = () => setOpen(!st.open);
    root.querySelector('.ask-head').addEventListener('click', e => { if (!st.open && !e.target.closest('button')) setOpen(true); });
    R.peek.onclick = e => { const b = e.target.closest('[data-pq]'); if (b) { setOpen(true); ask(b.dataset.pq, JSON.parse(b.dataset.ctx)); } };
    R.clear.onclick = () => { st.ctrl?.abort(); st.msgs = []; st.ctx = null; st.busy = false; save(); go('home'); };

    // free-text composer (local-model / Claude engines only)
    const grow = () => { R.box.style.height = 'auto'; R.box.style.height = Math.min(R.box.scrollHeight, 130) + 'px'; R.send.disabled = st.busy || !R.box.value.trim(); };
    R.box.oninput = grow;
    R.box.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); R.form.requestSubmit(); } };
    R.form.onsubmit = e => { e.preventDefault(); const t = R.box.value; if (!t.trim() || st.busy) return; R.box.value = ''; grow(); ask(t); };

    // ---------- load config + data, then show home
    setOpen(st.open);
    const [bank, status, trending] = await Promise.all([api('/api/chat/questions').catch(() => []), api('/api/chat/status').catch(() => st.status), api('/api/trends?hours=24&limit=10').catch(() => [])]);
    st.bank = bank; st.status = status; st.tickers = trending.map(t => ({ticker: t.ticker, name: t.name, tone: t.tone}));
    R.peek.innerHTML = '<span class="sec-lbl">Try</span>' + bank.flatMap(c => c.questions.filter(q => q.featured).map(q => `<button class="pop" data-pq="${esc(q.text)}" ${ctxAttr({kind: 'topic', id: c.id, text: q.text})}>${esc(q.text)}</button>`)).join('');
    const p = status.provider, free = p === 'ollama' || p === 'claude';
    R.badge.hidden = false;
    R.badge.textContent = {rules: '🧩 Built-in · no AI', ollama: '🦙 ' + status.model + ' · local', claude: '⚡ ' + status.model}[p] || status.model;
    R.badge.title = {rules: 'Answers come from rules over your own data — no AI model, no cost', ollama: 'Running on your machine via Ollama — no API cost', claude: 'Claude API — uses API credits'}[p] || '';
    if (status.reason) { R.banner.hidden = false; R.banner.textContent = 'ℹ ' + status.reason; }
    R.form.hidden = !free;
    if (free) R.sub.textContent = 'Pick a topic below, or type your own question.';
    if (status.web_search) { R.web.hidden = false; const paintW = () => { R.web.classList.toggle('on', st.web); R.web.setAttribute('aria-pressed', st.web); }; paintW();
      R.web.onclick = () => { st.web = !st.web; try { localStorage.setItem('chat:web', st.web ? '1' : '0'); } catch {} paintW(); App.toast(st.web ? '🌐 Web search on for your next questions' : 'Web search off'); }; }
    render(); grow();
    return {ask};
  }
  return {mount};
})();
