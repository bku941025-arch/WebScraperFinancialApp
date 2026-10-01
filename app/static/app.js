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

  const NAV = [['/', 'Dashboard'], ['/markets', 'Markets'], ['/feed', 'News & Releases'], ['/status', 'Scrape Status']];
  function chrome(active) {
    document.body.insertAdjacentHTML('afterbegin',
      `<div class="bg"><i></i><i></i><i></i></div>
       <nav class="nav"><div class="wrap"><a class="logo" href="/"><b>📈</b><span>FinTrend</span></a>
       ${NAV.map(([h, l]) => `<a class="link ${h === active ? 'on' : ''}" href="${h}">${l}</a>`).join('')}
       <span class="sp"></span><button class="icon-btn" id="theme" aria-label="Toggle theme"></button></div></nav>
       <div id="tip"></div><div id="toast"></div>`);
    document.body.insertAdjacentHTML('beforeend', '<footer>Headline tone is a rough keyword signal. Information only — not financial advice.</footer>');
    const b = $('#theme'), paint = () => b.textContent = isDark() ? '☀️' : '🌙';
    paint(); b.onclick = () => { const v = isDark() ? 'light' : 'dark'; document.documentElement.dataset.theme = v; store.set('theme', v); paint(); };
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
  return {$, esc, safeUrl, ago, clock, api, spark, tonePill, toneInfo, countUp, countdown, toast, ready, tape, srcBadge, store};
})();
