// Headless visual QA over the Chrome DevTools Protocol.
//
// The container has /usr/bin/chromium but no /opt/google/chrome/chrome, so the
// Playwright tooling cannot launch. This drives the system Chromium directly
// with Node's global WebSocket (Node >= 22, no npm dependencies).
//
//   node scripts/visual_qa.mjs <url> <width> <height> <out.png> [--full]
//
// It prints a JSON report: layout overflow, unnamed interactive controls,
// missing image alt text, and console errors. Read-only: it renders and
// measures; it never submits a form.

const CDP = "http://127.0.0.1:9222";

async function pageTarget() {
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
    } else if (msg.method) {
      events.push(msg);
    }
  });
  const ready = new Promise((r) => ws.addEventListener("open", r));
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const mid = ++id;
      pending.set(mid, { resolve, reject });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
  return { ready, send, events, close: () => ws.close() };
}

const [, , url, widthS, heightS, outPng, ...flags] = process.argv;
const width = Number(widthS), height = Number(heightS);
const full = flags.includes("--full");

const target = await pageTarget();
const cdp = connect(target.webSocketDebuggerUrl);
await cdp.ready;
await cdp.send("Page.enable");
await cdp.send("Runtime.enable");
await cdp.send("Log.enable");
await cdp.send("Emulation.setDeviceMetricsOverride", {
  width, height, deviceScaleFactor: 1, mobile: width < 500,
});

await cdp.send("Page.navigate", { url });
// Wait for the load event, then a short settle for CSS/JS.
await new Promise((r) => {
  const t = setTimeout(r, 4000);
  const check = setInterval(() => {
    if (cdp.events.some((e) => e.method === "Page.loadEventFired")) {
      clearInterval(check); clearTimeout(t); setTimeout(r, 600);
    }
  }, 100);
});

const measure = await cdp.send("Runtime.evaluate", {
  returnByValue: true,
  expression: `(() => {
    const de = document.documentElement;
    const overflow = de.scrollWidth - window.innerWidth;
    const unnamed = [...document.querySelectorAll('button,a,input,select,textarea')]
      .filter(el => {
        if (el.tagName === 'INPUT' && ['hidden','submit','button'].includes(el.type)) return false;
        const labelled = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        const name = (el.getAttribute('aria-label') || el.textContent || el.value ||
                      el.title || (labelled ? 'labelled' : '')).trim();
        return name.length === 0;
      })
      .map(el => el.tagName + '.' + (el.className || '').slice(0, 30));
    const noAlt = [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt')).length;
    const h1 = [...document.querySelectorAll('h1')].length;
    return { title: document.title, scrollWidth: de.scrollWidth, innerWidth: window.innerWidth,
             overflowPx: overflow, unnamedControls: unnamed.length, unnamedSample: unnamed.slice(0,5),
             imagesWithoutAlt: noAlt, h1Count: h1,
             bodyText: document.body.innerText.slice(0, 400) };
  })()`,
});

const shot = await cdp.send("Page.captureScreenshot", {
  format: "png", captureBeyondViewport: full,
});
const { writeFileSync } = await import("node:fs");
writeFileSync(outPng, Buffer.from(shot.data, "base64"));

const consoleErrors = cdp.events
  .filter((e) => e.method === "Log.entryAdded" && e.params.entry.level === "error")
  .map((e) => e.params.entry.text);

console.log(JSON.stringify({
  url, width, height, png: outPng,
  ...measure.result.value,
  consoleErrors,
}, null, 2));
cdp.close();
