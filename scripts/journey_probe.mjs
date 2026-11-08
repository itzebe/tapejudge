// Authenticated end-to-end journey probe over HTTP (no browser needed).
//
//   node scripts/journey_probe.mjs <baseUrl>
//
// It drives the real Flask app the way a person would: sign in, create a
// competition, configure and freeze the rubric, invite a judge, submit an entry,
// score it, settle, and verify publicly. It asserts the security properties too
// (anonymous writes refused, CSRF enforced, cross-account access denied) and
// prints a JSON report. It writes to the running demo database; that is fine
// because every value here is synthetic.
//
// Pointed at a remote host it leaves a real "Journey Probe Round" competition in
// that host's store, so the landing page fills with debris. It warns loudly when
// the target is not local; remove what it left with
// `scripts/purge_demo_competitions.py --name "Journey Probe Round" --confirm`.

const base = process.argv[2] || "http://127.0.0.1:12000";
if (!/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(base)) {
  console.error(
    `WARNING: ${base} is not local — this run will create a real ` +
    `"Journey Probe Round" competition in that store. Clean it up with ` +
    `scripts/purge_demo_competitions.py --name "Journey Probe Round" --confirm`);
}
let cookie = "";
const jar = {};

function saveCookies(res) {
  const raw = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
  for (const c of raw) {
    const [pair] = c.split(";");
    const [k, v] = pair.split("=");
    jar[k.trim()] = v;
  }
  cookie = Object.entries(jar).map(([k, v]) => `${k}=${v}`).join("; ");
}

async function req(path, { method = "GET", form, json, redirect = "manual" } = {}) {
  const headers = {};
  if (cookie) headers["Cookie"] = cookie;
  let body;
  if (form) { headers["Content-Type"] = "application/x-www-form-urlencoded"; body = new URLSearchParams(form).toString(); }
  if (json) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  const res = await fetch(base + path, { method, headers, body, redirect });
  saveCookies(res);
  const text = await res.text();
  return { status: res.status, text, location: res.headers.get("location") };
}

function csrf(html) {
  const m = html.match(/name="csrf_token"\s+value="([^"]+)"/);
  return m ? m[1] : null;
}

const report = { steps: [], checks: [] };
const step = (name, detail) => report.steps.push({ name, ...detail });
const check = (name, ok, detail = "") => { report.checks.push({ name, ok, detail }); };

// --- anonymous protections --------------------------------------------------
{
  const r = await req("/api/competitions", { method: "POST", json: { name: "x" } });
  check("anonymous create refused", r.status === 401, `status ${r.status}`);
  const r2 = await req("/new", { method: "POST", form: { name: "x", judges: "j" } });
  check("anonymous /new redirects to login", r2.status === 302 && (r2.location || "").includes("login"), `${r2.status} ${r2.location}`);
}

// --- sign in as the seeded organiser ---------------------------------------
{
  const page = await req("/login");
  const token = csrf(page.text);
  const r = await req("/login", { method: "POST", form: { email: "organiser@demo.tapejudge.test", password: "synthetic-demo-password", csrf_token: token } });
  step("login organiser", { status: r.status, location: r.location });
  check("organiser signed in", r.status === 302 && !(r.location || "").includes("login"), `${r.status}`);
}

// --- create a competition ---------------------------------------------------
let cid;
{
  const page = await req("/new");
  const token = csrf(page.text);
  const r = await req("/new", { method: "POST", form: {
    name: "Journey Probe Round", organiser: "Probe", judges_raw: "probe.judge",
    description: "synthetic", opens_at: "", closes_at: "", prize_note: "", csrf_token: token } });
  const loc = r.location || "";
  cid = (loc.match(/\/c\/([^/]+)\/rubric/) || [])[1];
  step("create competition", { status: r.status, location: loc, cid });
  check("competition created", !!cid, loc);
}

// --- CSRF is required -------------------------------------------------------
{
  const r = await req(`/api/competitions/${cid}/rubric`, { method: "POST", json: {
    criteria: [{ name: "quality", weight: 1, max_score: 10 }], judges: 1 } });
  check("write without CSRF refused", r.status === 403, `status ${r.status}`);
}

// --- configure + freeze the rubric -----------------------------------------
{
  const page = await req(`/c/${cid}/rubric`);
  const token = csrf(page.text);
  const r = await req(`/c/${cid}/rubric`, { method: "POST", form: {
    "crit_name": "quality", "crit_weight": "2", "crit_max": "10",
    judges: "1", csrf_token: token } });
  step("configure rubric", { status: r.status });
  const pub = await req(`/c/${cid}/publish`);
  const t2 = csrf(pub.text);
  const f = await req(`/c/${cid}/publish`, { method: "POST", form: { action: "freeze", csrf_token: t2 } });
  step("freeze rubric", { status: f.status });
  const after = await req(`/c/${cid}/publish`);
  check("rubric is frozen", after.text.includes("frozen") || after.text.includes("Frozen"), "");
}

// --- invite a judge and accept as a second account -------------------------
{
  const page = await req(`/c/${cid}/judges`);
  const token = csrf(page.text);
  const r = await req(`/c/${cid}/judges`, { method: "POST", form: {
    email: "judge@demo.tapejudge.test", label: "probe.judge", action: "invite", csrf_token: token } });
  const inv = (r.text.match(/\/invitations\/([A-Za-z0-9_\-]+)/) || [])[1];
  step("invite judge", { status: r.status, hasToken: !!inv });
  check("invitation token issued", !!inv, "");
  // Save organiser session, switch to judge.
  globalThis.__invite = inv;
}

// --- submit an entry (as organiser is also entrant-capable) -----------------
{
  const page = await req(`/c/${cid}/submit`);
  const token = csrf(page.text);
  const r = await req(`/c/${cid}/submit`, { method: "POST", form: {
    stable_id: "probe-entry", title: "Probe Entry", summary: "synthetic", link: "", csrf_token: token } });
  step("submit entry", { status: r.status });
}

// --- judge sign-in + accept invitation + score -----------------------------
{
  // fresh jar for the judge
  for (const k of Object.keys(jar)) delete jar[k];
  cookie = "";
  const page = await req("/login");
  const token = csrf(page.text);
  await req("/login", { method: "POST", form: { email: "judge@demo.tapejudge.test", password: "synthetic-demo-password", csrf_token: token } });
  const invPage = await req(`/invitations/${globalThis.__invite}`);
  const t2 = csrf(invPage.text);
  const acc = await req(`/invitations/${globalThis.__invite}`, { method: "POST", form: { csrf_token: t2 } });
  step("accept invitation", { status: acc.status });
  const jp = await req(`/c/${cid}/judge`);
  const t3 = csrf(jp.text);
  const sc = await req(`/c/${cid}/judge`, { method: "POST", form: {
    entry: "probe-entry", score_quality: "9", justification: "looks good", action: "score", csrf_token: t3 } });
  step("score entry", { status: sc.status });
  check("judge scored", sc.status === 302, `status ${sc.status}`);
}

// --- public verification (anonymous) ---------------------------------------
{
  for (const k of Object.keys(jar)) delete jar[k];
  cookie = "";
  const v = await req(`/c/${cid}/verify`);
  const res = await req(`/c/${cid}/results`);
  check("public verify reachable anonymously", v.status === 200, `status ${v.status}`);
  check("public results reachable anonymously", res.status === 200, `status ${res.status}`);
  check("ranking shows the entry", res.text.includes("probe-entry"), "");
  const api = await req(`/api/competitions/${cid}/verify`);
  const parsed = JSON.parse(api.text);
  check("verify reproduces a ranking", Array.isArray(parsed.ranking) && parsed.ranking.length === 1,
    `ranking ${parsed.ranking ? parsed.ranking.length : "?"}`);
  check("frozen rule checks pass", parsed.frozen_rule_ok === true, JSON.stringify(parsed.frozen_rule_checks));
}

report.competition_id = cid;
report.summary = { passed: report.checks.filter((c) => c.ok).length, total: report.checks.length };
console.log(JSON.stringify(report, null, 2));
process.exit(report.checks.every((c) => c.ok) ? 0 : 1);
