// Accessibility and layout audit over the Chrome DevTools Protocol.
//
//   node scripts/a11y_audit.mjs <baseUrl> [path ...]
//
// The container has no Chrome bundle for the Playwright tooling, so this drives
// the system Chromium directly. It measures what can be measured honestly:
// horizontal overflow, tap-target sizes, text contrast, heading order, image
// alt text, control names, and the document language. It is not a substitute
// for looking at the page; it is the machine-checkable part of that review.
//
// Read-only: it loads pages and measures; it never submits a form.

const CDP = "http://127.0.0.1:9222";
const base = process.argv[2] || "http://127.0.0.1:12000";
const paths = process.argv.slice(3);
if (paths.length === 0) paths.push("/");

async function target() {
  const list = await (await fetch(`${CDP}/json`)).json();
  return list.find((t) => t.type === "page");
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  const events = [];
  ws.addEventListener("message", (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
    } else if (msg.method) events.push(msg);
  });
  const ready = new Promise((r) => ws.addEventListener("open", r));
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const mid = ++id; pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  return { ready, send, events, close: () => ws.close() };
}

const AUDIT = `(() => {
  const luminance = (rgb) => {
    const [r, g, b] = rgb.map(v => { v /= 255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); });
    return 0.2126*r + 0.7152*g + 0.0722*b;
  };
  const parse = (c) => (c.match(/\\d+(\\.\\d+)?/g) || []).slice(0,3).map(Number);
  const alpha = (c) => { const m = c.match(/rgba?\\([^)]*\\)/); if (!m) return 1; const p = m[0].match(/[\\d.]+/g); return p && p.length === 4 ? Number(p[3]) : 1; };
  const contrast = (fg, bg) => {
    const L1 = luminance(parse(fg)), L2 = luminance(parse(bg));
    return (Math.max(L1,L2) + 0.05) / (Math.min(L1,L2) + 0.05);
  };
  const bgOf = (el) => {
    let n = el;
    while (n && n !== document.documentElement) {
      const b = getComputedStyle(n).backgroundColor;
      if (b && b !== 'rgba(0, 0, 0, 0)' && alpha(b) > 0) return b;
      n = n.parentElement;
    }
    return 'rgb(255,255,255)';
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };

  const de = document.documentElement;
  const overflow = de.scrollWidth - window.innerWidth;

  // Small tap targets (WCAG 2.5.8 minimum is 24x24 CSS px).
  const small = [];
  document.querySelectorAll('a,button,input,select,textarea,summary').forEach(el => {
    if (!visible(el)) return;
    const r = el.getBoundingClientRect();
    if (r.width < 24 || r.height < 24) {
      const inline = getComputedStyle(el).display === 'inline' && el.closest('p,li,blockquote,dd');
      small.push({ tag: el.tagName, text: (el.textContent||el.value||'').trim().slice(0,30),
                   w: Math.round(r.width), h: Math.round(r.height), inlineProse: !!inline });
    }
  });

  // Text contrast (4.5:1 normal, 3:1 large).
  const lowContrast = [];
  document.querySelectorAll('p,li,td,th,dt,dd,a,span,small,label,h1,h2,h3,button').forEach(el => {
    if (!visible(el)) return;
    if (!el.textContent || !el.textContent.trim()) return;
    if ([...el.children].some(c => c.textContent && c.textContent.trim())) return; // leaf only
    const s = getComputedStyle(el);
    const size = parseFloat(s.fontSize);
    const large = size >= 24 || (size >= 18.66 && parseInt(s.fontWeight) >= 700);
    const ratio = contrast(s.color, bgOf(el));
    const need = large ? 3 : 4.5;
    if (ratio < need) lowContrast.push({ text: el.textContent.trim().slice(0,30), ratio: +ratio.toFixed(2), need });
  });

  // Heading order.
  const heads = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => Number(h.tagName[1]));
  let skip = null;
  for (let i = 1; i < heads.length; i++) if (heads[i] > heads[i-1] + 1) { skip = heads[i-1] + '->' + heads[i]; break; }

  const noAlt = [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt')).length;
  const unnamed = [...document.querySelectorAll('button,a,input,select,textarea')].filter(el => {
    if (el.tagName === 'INPUT' && ['hidden','submit','button'].includes(el.type)) return false;
    const labelled = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    return !(el.getAttribute('aria-label') || el.textContent.trim() || el.value || el.title || labelled);
  }).length;

  return { lang: de.lang || null, overflow, h1Count: heads.filter(h => h === 1).length,
           headingSkip: skip, smallTargets: small.length,
           smallTargetsNonProse: small.filter(s => !s.inlineProse).length,
           smallSample: small.filter(s => !s.inlineProse).slice(0,6),
           lowContrast: lowContrast.length, lowContrastSample: lowContrast.slice(0,6),
           imagesWithoutAlt: noAlt, unnamedControls: unnamed };
})()`;

const targetInfo = await target();
const cdp = connect(targetInfo.webSocketDebuggerUrl);
await cdp.ready;
await cdp.send("Page.enable");
await cdp.send("Runtime.enable");

const out = [];
for (const p of paths) {
  for (const [w, h] of [[390, 900], [768, 900], [1440, 1000]]) {
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
    await cdp.send("Page.navigate", { url: base + p });
    await new Promise((r) => {
      const t = setTimeout(r, 4000);
      const c = setInterval(() => {
        if (cdp.events.some((e) => e.method === "Page.loadEventFired")) { clearInterval(c); clearTimeout(t); setTimeout(r, 400); }
      }, 100);
    });
    const m = await cdp.send("Runtime.evaluate", { returnByValue: true, expression: AUDIT });
    out.push({ path: p, width: w, ...m.result.value });
  }
}

const fails = out.filter((r) => r.overflow > 0 || r.smallTargetsNonProse > 0 || r.lowContrast > 0 ||
  r.headingSkip || r.h1Count !== 1 || r.imagesWithoutAlt > 0 || r.unnamedControls > 0 || !r.lang);
console.log(JSON.stringify({ base, pages: out.length, failing: fails.length, results: out }, null, 2));
cdp.close();
