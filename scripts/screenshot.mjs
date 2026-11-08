// Full-page screenshots over the Chrome DevTools Protocol.
//
//   node scripts/screenshot.mjs <baseUrl> <outDir> <path> [width] [label]
//
// Drives the system Chromium (the container has no Chrome bundle for the
// Playwright tooling) and writes a full-page PNG per (path, width). Used for
// the design QA recorded in docs/FINAL_RELEASE_REPORT.md. Read-only.

import { writeFileSync, mkdirSync } from "node:fs";

const CDP = "http://127.0.0.1:9222";
const [base, outDir, ...rest] = process.argv.slice(2);
if (!base || !outDir || rest.length === 0) {
  console.error("usage: node scripts/screenshot.mjs <baseUrl> <outDir> <path> [width] [label]");
  process.exit(2);
}
const path = rest[0];
const width = Number(rest[1] || 1280);
const label = rest[2] || path.replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "") || "root";
mkdirSync(outDir, { recursive: true });

async function pageTarget() {
  const list = await (await fetch(`${CDP}/json`)).json();
  return list.find((t) => t.type === "page");
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
    }
  });
  const ready = new Promise((r) => ws.addEventListener("open", r));
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const mid = ++id; pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  return { ready, send, close: () => ws.close() };
}

const t = await pageTarget();
const cdp = connect(t.webSocketDebuggerUrl);
await cdp.ready;
await cdp.send("Page.enable");
await cdp.send("Emulation.setDeviceMetricsOverride", {
  width, height: 900, deviceScaleFactor: 2, mobile: width <= 480,
});
await cdp.send("Page.navigate", { url: base + path });
await new Promise((r) => setTimeout(r, 1400));
// Reveal-on-scroll sections are revealed by the page script on intersection;
// scroll through the document so a full-page capture shows them, then return.
await cdp.send("Runtime.evaluate", {
  expression: `(async () => {
    for (let y = 0; y < document.body.scrollHeight; y += 400) {
      window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60));
    }
    window.scrollTo(0, 0);
  })()`, awaitPromise: true,
});
await new Promise((r) => setTimeout(r, 400));
const shot = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
const file = `${outDir}/${label}-${width}.png`;
writeFileSync(file, Buffer.from(shot.data, "base64"));
console.log(`wrote ${file}`);
cdp.close();
process.exit(0);
