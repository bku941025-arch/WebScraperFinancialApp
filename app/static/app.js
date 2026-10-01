/* Shared helpers for every page. No dependencies. */
const App = (() => {
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeUrl = u => /^https?:\/\//i.test(u) ? esc(u) : '#';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ago = t => { const m = Math.max(0, (Date.now() / 1000 - t) / 60 | 0);
    return m < 1 ? 'just now' : m < 60 ? m + 'm ago' : m < 1440 ? (m / 60 | 0) + 'h ago' : (m / 1440 | 0) + 'd ago'; };
  const clock = t => new Date(t * 1000).toLocaleString([], {weekday: 'short', hour: '2-digit', minute: '2-digit'});
  const api = async (path, opts) => { const r = await fetch(path, opts); if (!r.ok) throw new Error(path + ' → ' + r.status); return r.json(); };
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, v); } catch {} } };

  // theme (set before first paint)
  const saved = store.get('theme'); if (saved) document.documentElement.dataset.theme = saved;
  const isDark = () => (document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')) === 'dark';

  let sid = 0;
  function spark(vals, {w = 120, h = 40} = {}) {
    const n = vals.length, max = Math.max(1, ...vals), id = 'sg' + sid++;
    const pts = vals.map((v, i) => [i / (n - 1 || 1) * w, h - 3 - v / max * (h - 8)]);
    const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('');
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".35"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs><path d="${line}L${w} ${h}L0 ${h}Z" fill="url(#${id})"/><path class="ln" d="${line}" pathLength="1" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
  }
  const toneInfo = s => s > .15 ? ['up', '▲ Bullish'] : s < -.15 ? ['down', '▼ Bearish'] : ['', '● Neutral'];
  const tonePill = s => { const [c, l] = toneInfo(s);
    return `<span class="tag ${c}" data-tip="Rough keyword tone of headlines — not investment advice">${l}</span>`; };

  function countUp(el, to) {
    if (reduced || !isFinite(to)) { el.textContent = to; return; }
    const from = +el.dataset.v || 0, t0 = performance.now(); el.dataset.v = to;
    const step = t => { const k = Math.min(1, (t - t0) / 700), e = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(from + (to - from) * e).toLocaleString(); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }
  const fmtCd = s => { s = Math.max(0, s | 0); const d = s / 86400 | 0, h = s % 86400 / 3600 | 0, m = s % 3600 / 60 | 0;
    return (d ? d + 'd ' : '') + (h || d ? h + 'h ' : '') + m + 'm ' + String(s % 60).padStart(2, '0') + 's'; };
  function countdown(el, ts) { const tick = () => el.textContent = fmtCd(ts - Date.now() / 1000); tick(); return setInterval(tick, 1000); }

  let toastT;
  function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show');
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 2800); }


  // ---------- formatting
  const CUR = {USD: '$', EUR: '€', GBP: '£', JPY: '¥', INR: '₹', HKD: 'HK$', KRW: '₩', CNY: '¥', AUD: 'A$', CAD: 'C$'};
  const money = (v, cur = 'USD') => v == null ? '–' : (CUR[cur] ?? cur + ' ') + Number(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: v >= 1000 ? 0 : 2});
  const compact = v => v == null ? '–' : Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 1}).format(v);
  const pctTxt = v => (v > 0 ? '+' : '') + v.toFixed(2) + '%';
  const dirCls = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
  const dayLabel = iso => { const d = new Date(iso + 'T12:00:00'), t = new Date(); t.setHours(12, 0, 0, 0);
    const diff = Math.round((d - t) / 864e5);
    return (diff === 0 ? 'Today · ' : diff === 1 ? 'Tomorrow · ' : '') + d.toLocaleDateString([], {weekday: 'long', month: 'short', day: 'numeric'}); };
  const shortDay = iso => new Date(iso + 'T12:00:00').toLocaleDateString([], {month: 'short', day: 'numeric'});

  // ---------- price chart (SVG, line or candlesticks, hover crosshair)
  let cid = 0;
  function priceChart(host, data, {mode = 'line', h = 240} = {}) {
    const cs = data.candles || [];
    if (!cs.length) { host.className = 'pc'; host.innerHTML = `<div class="empty" style="padding:30px 10px">${esc(data.error || 'No price data')}</div>`; return; }
    const W = Math.max(300, host.clientWidth || 600), padR = 58, padT = 8, padB = 22, volH = 34;
    const pw = W - padR, ph = h - padT - padB - volH - 6, n = cs.length, bw = pw / n;
    const intraday = data.range === '1D' || data.range === '5D';
    const lo0 = Math.min(...cs.map(c => c[3])), hi0 = Math.max(...cs.map(c => c[2])), pad = (hi0 - lo0) * .08 || hi0 * .01;
    const lo = lo0 - pad, hi = hi0 + pad, maxV = Math.max(1, ...cs.map(c => c[5]));
    const X = i => (i + .5) * bw, Y = v => padT + (hi - v) / (hi - lo) * ph;
    const up = cs[n - 1][4] >= cs[0][4], cur = (data.meta || {}).currency || 'USD';
    const fmtP = v => Number(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const when = (t, long) => { const d = new Date(t * 1000);
      return intraday ? d.toLocaleString([], long ? {weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'} : (data.range === '5D' ? {weekday: 'short', hour: '2-digit', minute: '2-digit'} : {hour: '2-digit', minute: '2-digit'}))
                      : d.toLocaleDateString([], data.range === '1Y' ? {month: 'short', year: '2-digit'} : {month: 'short', day: 'numeric'}); };
    const gid = 'pg' + cid++;
    let g = '';
    for (let k = 0; k <= 3; k++) { const v = lo + (hi - lo) * k / 3, y = Y(v);
      g += `<line class="gl" x1="0" x2="${pw}" y1="${y}" y2="${y}"/><text x="${pw + 6}" y="${y + 4}">${fmtP(v)}</text>`; }
    let xl = ''; for (let k = 0; k < 4; k++) { const i = Math.round((n - 1) * k / 3);
      xl += `<text x="${Math.min(Math.max(X(i), 20), pw - 20)}" y="${h - 6}" text-anchor="middle">${when(cs[i][0])}</text>`; }
    const vols = cs.map((c, i) => `<rect class="vol" x="${i * bw + bw * .15}" width="${Math.max(1, bw * .7)}" y="${h - padB - c[5] / maxV * volH}" height="${c[5] / maxV * volH}"/>`).join('');
    let body;
    if (mode === 'candle') {
      body = cs.map((c, i) => { const col = c[4] >= c[1] ? 'var(--up)' : 'var(--down)', top = Y(Math.max(c[1], c[4])), bot = Y(Math.min(c[1], c[4]));
        return `<line x1="${X(i)}" x2="${X(i)}" y1="${Y(c[2])}" y2="${Y(c[3])}" stroke="${col}" stroke-width="1.2"/><rect x="${X(i) - Math.max(1, bw * .35)}" y="${top}" width="${Math.max(2, bw * .7)}" height="${Math.max(1, bot - top)}" fill="${col}" rx="1"/>`; }).join('');
    } else {
      const d = cs.map((c, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(c[4]).toFixed(1)).join('');
      body = `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--c)" stop-opacity=".30"/><stop offset="1" stop-color="var(--c)" stop-opacity="0"/></linearGradient></defs>
        <path d="${d}L${X(n - 1)} ${padT + ph}L${X(0)} ${padT + ph}Z" fill="url(#${gid})"/><path class="ln2" d="${d}" pathLength="2000" style="--len:2000"/>`;
    }
    const last = cs[n - 1][4], ly = Y(last);
    const tag = `<rect x="${pw + 2}" y="${ly - 9}" width="${padR - 4}" height="18" rx="5" fill="var(--c)"/><text x="${pw + padR / 2}" y="${ly + 4}" text-anchor="middle" style="fill:#fff">${fmtP(last)}</text>`;
    host.className = 'pc ' + (up ? 'up' : 'down');
    host.innerHTML = `<svg viewBox="0 0 ${W} ${h}" height="${h}">${g}${xl}${vols}${body}${tag}
      <line class="cur" id="cx${gid}" y1="${padT}" y2="${padT + ph}"/><rect id="ov${gid}" x="0" y="0" width="${pw}" height="${h}" fill="transparent"/></svg><div class="pcTip"></div>`;
    const ov = host.querySelector('rect[id^=ov]'), cx = host.querySelector('.cur'), tip = host.querySelector('.pcTip');
    ov.onpointermove = e => { const r = ov.getBoundingClientRect(), i = Math.min(n - 1, Math.max(0, Math.floor((e.clientX - r.left) / r.width * n))), c = cs[i];
      cx.setAttribute('x1', X(i)); cx.setAttribute('x2', X(i));
      tip.innerHTML = `<span style="opacity:.7">${esc(when(c[0], true))}</span><br>` + `O ${fmtP(c[1])} · H ${fmtP(c[2])}<br>L ${fmtP(c[3])} · <b>C ${fmtP(c[4])}</b>` + `<br><span style="opacity:.7">Vol ${compact(c[5])}</span>`;
      tip.style.left = Math.min(Math.max(X(i), 70), W - padR - 40) + 'px'; tip.style.top = Math.max(Y(c[4]) - 10, 60) + 'px'; tip.style.opacity = 1; };
    ov.onpointerleave = () => tip.style.opacity = 0;
  }

  // ---------- stock panel: range chips + line/candle toggle + chart, lazy-loaded
  const chartCache = new Map();
  function stockPanel(host, symbol, {h = 240, ranges = ['1D', '5D', '1M', '6M', '1Y'], range = '1M'} = {}) {
    let mode = store.get('chartMode') || 'line', data = null, token = 0;
    host.classList.add('sp');
    host.innerHTML = `<div class="sp-head"><div class="chips" data-k="range">${ranges.map(r => `<button class="chip sm ${r === range ? 'on' : ''}" data-v="${r}">${r}</button>`).join('')}</div>
      <div class="chips seg" data-k="mode">${[['line', 'Line'], ['candle', 'Candles']].map(([v, l]) => `<button class="chip sm ${v === mode ? 'on' : ''}" data-v="${v}">${l}</button>`).join('')}</div></div>
      <div class="pc"><div class="sk-chart"></div></div><div class="src sp-foot"></div>`;
    const pc = $('.pc', host), foot = $('.sp-foot', host);
    const draw = () => { if (!data) return; priceChart(pc, data, {mode, h});
      const c = data.candles; const ch = c.length > 1 ? (c[c.length - 1][4] / c[0][4] - 1) * 100 : null;
      const note = data.source === 'demo' ? '<span class="warn">⚠ Synthetic demo prices — not real market data</span>' : `Prices: Yahoo Finance (unofficial, may be delayed) · ${data.range === '1D' || data.range === '5D' ? 'local time' : 'daily closes'}${data.stale ? ' · <span class="warn">cached copy (refresh failed)</span>' : ''}`;
      foot.innerHTML = `<span>${note}</span>${ch == null ? '' : `<span class="tag ${dirCls(ch)}">${range} change ${pctTxt(ch)}</span>`}`; };
    async function load() { const my = ++token, key = symbol + '|' + range; let hit = chartCache.get(key);
      if (!hit || Date.now() - hit.t > 60e3) { pc.innerHTML = '<div class="sk-chart"></div>';
        try { const d = await api(`/api/stock/${encodeURIComponent(symbol)}/chart?range=${range}`); hit = {d, t: Date.now()}; if (!d.error) chartCache.set(key, hit); }
        catch { hit = {d: {candles: [], error: 'Couldn’t reach the price API'}}; } }
      if (my !== token) return; data = hit.d; draw(); }
    host.onclick = e => { const b = e.target.closest('.chip'); if (!b) return; const k = b.parentElement.dataset.k;
      if (k === 'range') range = b.dataset.v; else { mode = b.dataset.v; store.set('chartMode', mode); }
      b.parentElement.querySelectorAll('.chip').forEach(x => x.classList.toggle('on', x === b)); k === 'range' ? load() : draw(); };
    let rt; new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => host.isConnected && data && host.clientWidth && draw(), 150); }).observe(host);
    load();
  }

  // ---------- stock summary card
  function rangeBar(lo, hi, v, cur) { const p = hi > lo ? Math.min(100, Math.max(0, (v - lo) / (hi - lo) * 100)) : 50;
    return `<div class="rng"><span>${money(lo, cur)}</span><div class="rbar"><i style="left:${p}%"></i></div><span>${money(hi, cur)}</span></div>`; }
  function summaryHTML(s, {compactMode = false} = {}) {
    const q = s.quote;
    if (!q) return `<div class="tk" style="font-size:18px">${esc(s.symbol)}</div><div class="nm">${esc(s.name)}</div><div class="src" style="margin-top:10px">${esc(s.error || 'No price data available')}</div>
      ${s.mentions_24h ? `<div class="tag" style="margin-top:8px;display:inline-block">${s.mentions_24h} mentions in 24h</div>` : ''}`;
    const e = s.next_earnings, f = s.latest_filing;
    return `<div class="row" style="display:flex;justify-content:space-between;gap:8px;align-items:baseline"><div><span class="tk" style="font-size:${compactMode ? 18 : 22}px">${esc(s.symbol)}</span> <span class="nm">${esc(s.name)}</span></div>
        <span class="src">${esc(q.exchange || '')}</span></div>
      <div class="px ${compactMode ? 'sm' : ''}" style="margin-top:6px">${money(q.price, q.currency)} <span class="tag ${dirCls(q.change)}" style="font-size:13px;vertical-align:middle">${q.change > 0 ? '▲' : q.change < 0 ? '▼' : ''} ${q.change > 0 ? '+' : ''}${q.change.toFixed(2)} (${pctTxt(q.change_pct)})</span></div>
      <div class="stats2">
        <div class="wide"><div class="k">Day range</div>${rangeBar(q.day_low, q.day_high, q.price, q.currency)}</div>
        <div class="wide"><div class="k">52-week range</div>${rangeBar(q.year_low, q.year_high, q.price, q.currency)}</div>
        <div><div class="k">Volume</div><div class="v">${compact(q.volume)} <span class="src">avg ${compact(q.avg_volume)}</span></div></div>
        <div><div class="k">YTD</div><div class="v ${dirCls(q.ytd_pct)}" style="color:var(--${q.ytd_pct >= 0 ? 'up' : 'down'})">${pctTxt(q.ytd_pct)}</div></div>
        <div><div class="k">News (24h)</div><div class="v">${s.mentions_24h} mention${s.mentions_24h === 1 ? '' : 's'}</div></div>
        <div><div class="k">Next earnings</div><div class="v">${e ? `${esc(shortDay(e.date))} <span class="src">${esc(e.time_label)}</span>` : '<span class="src">not scheduled</span>'}</div></div>
        ${!compactMode && f ? `<div class="wide"><div class="k">Latest SEC filing</div><a class="v" href="${safeUrl(f.url)}" target="_blank" rel="noopener" style="color:var(--a1)">${esc(f.title)}</a> <span class="src">${ago(f.published_at)}</span></div>` : ''}
      </div>
      ${s.source === 'demo' ? '<div class="src warn" style="margin-top:10px">⚠ Synthetic demo prices</div>' : s.stale ? '<div class="src warn" style="margin-top:10px">Cached data (refresh failed)</div>' : ''}`;
  }
  const sumCache = new Map();
  async function stockSummary(sym) { const c = sumCache.get(sym); if (c && Date.now() - c.t < 60e3) return c.d;
    const d = await api(`/api/stock/${encodeURIComponent(sym)}/summary`); sumCache.set(sym, {d, t: Date.now()}); return d; }

  // ---------- global ticker search with live summary peek
  function search() {
    const input = $('#gs'), drop = $('#sdrop'), res = $('#sres'), peek = $('#peek');
    let items = [], act = 0, qTok = 0, pTok = 0, tmr, ptmr;
    const close = () => { drop.hidden = true; input.setAttribute('aria-expanded', 'false'); };
    const go = t => { location.href = '/company/' + encodeURIComponent(t); };
    async function showPeek() { const it = items[act]; clearTimeout(ptmr); if (!it) { peek.innerHTML = ''; return; }
      peek.innerHTML = `<div class="sk-chart" style="height:130px"></div>`; const my = ++pTok;
      ptmr = setTimeout(async () => { try { const s = await stockSummary(it.ticker); if (my === pTok) peek.innerHTML = summaryHTML(s, {compactMode: true}) + `<a class="tag" style="display:inline-block;margin-top:12px" href="/company/${encodeURIComponent(it.ticker)}">Open full page & chart →</a>`; }
        catch { if (my === pTok) peek.innerHTML = '<div class="src">Summary unavailable.</div>'; } }, 180); }
    function paint() { res.innerHTML = items.map((it, i) => `<a role="option" class="${i === act ? 'act' : ''}" data-i="${i}"><b>${esc(it.ticker)}</b><span>${esc(it.name)}</span></a>`).join('') || '<div class="none">No matches. Try a ticker like AAPL or 0700.HK</div>'; }
    input.oninput = () => { clearTimeout(tmr); const q = input.value.trim(); if (!q) { close(); return; }
      tmr = setTimeout(async () => { const my = ++qTok; try { const r = await api('/api/search?q=' + encodeURIComponent(q)); if (my !== qTok) return;
        items = r; act = 0; paint(); drop.hidden = false; input.setAttribute('aria-expanded', 'true'); showPeek(); } catch {} }, 150); };
    input.onkeydown = e => { if (e.key === 'Escape') { close(); input.blur(); } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); if (!items.length) return;
        act = (act + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length; paint(); showPeek(); }
      else if (e.key === 'Enter') { const t = items[act]?.ticker || input.value.trim().toUpperCase(); if (t) go(t); } };
    res.onmousemove = e => { const a = e.target.closest('a'); if (a && +a.dataset.i !== act) { act = +a.dataset.i; paint(); showPeek(); } };
    res.onclick = e => { const a = e.target.closest('a'); if (a) go(items[+a.dataset.i].ticker); };
    input.onfocus = () => { if (items.length && input.value.trim()) drop.hidden = false; };
    document.addEventListener('click', e => { if (!e.target.closest('.sbox')) close(); });
    document.addEventListener('keydown', e => { if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); input.focus(); input.select(); } });
  }

  const NAV = [['/', 'Dashboard'], ['/markets', 'Markets'], ['/earnings', 'Earnings'], ['/feed', 'News & Filings'], ['/status', 'Status']];
  function chrome(active) {
    document.body.insertAdjacentHTML('afterbegin',
      `<div class="bg"><i></i><i></i><i></i></div>
       <nav class="nav"><div class="wrap"><a class="logo" href="/"><b>📈</b><span>FinTrend</span></a>
       ${NAV.map(([h, l]) => `<a class="link ${h === active ? 'on' : ''}" href="${h}">${l}</a>`).join('')}
       <div class="sbox"><span class="mag">🔍</span><input id="gs" placeholder="Search ticker or company…" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="false" aria-controls="sdrop"><kbd>/</kbd>
         <div id="sdrop" hidden><div class="sres" id="sres" role="listbox"></div><div class="peek" id="peek"></div></div></div>
       <button class="icon-btn" id="theme" aria-label="Toggle theme"></button></div></nav>
       <div id="tip"></div><div id="toast"></div>`);
    document.body.insertAdjacentHTML('beforeend', '<footer>Headline tone is a rough keyword signal. Information only — not financial advice.</footer>');
    const b = $('#theme'), paint = () => b.textContent = isDark() ? '☀️' : '🌙';
    paint(); b.onclick = () => { const v = isDark() ? 'light' : 'dark'; document.documentElement.dataset.theme = v; store.set('theme', v); paint(); };
    search();
    const tip = $('#tip');
    document.addEventListener('mousemove', e => { const t = e.target.closest?.('[data-tip]');
      tip.style.opacity = t ? 1 : 0; if (!t) return; tip.textContent = t.dataset.tip;
      tip.style.left = Math.min(innerWidth - 270, e.clientX + 14) + 'px'; tip.style.top = e.clientY + 18 + 'px'; });
  }
  const ready = (active, fn) => document.addEventListener('DOMContentLoaded', () => { chrome(active); fn(); });
  const tape = items => `<div class="tape"><div class="track">${[0, 1].map(() => items.map(t =>
      `<a href="/company/${encodeURIComponent(t.ticker)}"><b>${esc(t.ticker)}</b><span class="src">${esc(t.name)}</span>${
      tonePill(t.tone)}</a>`).join('')).join('')}</div></div>`;
  const hue = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  const srcBadge = s => `<span class="badge" style="background:hsl(${hue(s)} 62% 46%)">${esc(s)}</span>`;
  return {$, esc, safeUrl, ago, clock, api, spark, tonePill, toneInfo, countUp, countdown, toast, ready, tape, srcBadge, store, hue,
          money, compact, pctTxt, dirCls, dayLabel, shortDay, priceChart, stockPanel, summaryHTML, stockSummary};
})();
