# TapeJudge — project memory

Agent-written notes about `/workspace/tapejudge`. Verify before relying on them.

## What it is

Standalone Flask + SQLite MVP: a judged-competition scoring app whose scoring
rule is compiled to a TapeOut circuit and frozen, and whose ranking is
recomputed from stored inputs on every read (no stored result). Vendored Mission
3 core (`netlist/rubric/engine/verifier/lifecycle/chain`) is byte-faithful — do
not reformat. Own local git repo, **no remote**.

## Commands that matter

- Offline suite: `TAPEJUDGE_SKIP_CHAIN=1 python3 -m unittest discover -s tests`
  (7 live-chain tests skip).
- Full suite (network): `python3 -m unittest discover -s tests`.
- Lint: `python3 -m ruff check .` (line-length 100; per-file ignores for the
  vendored core in `pyproject.toml`).
- Demo: `TAPEJUDGE_SKIP_CHAIN=1 python3 demo/run_demo.py`.
- Chain evidence: `python3 scripts/capture_chain_evidence.py` (read-only).

## Environment quirks (learned the hard way)

- **`/opt/google/chrome/chrome` does not exist**, so the `playwright_browser_*`
  tools fail. There IS a system `/usr/bin/chromium`; drive it headless over the
  DevTools Protocol with Node 24's global `WebSocket` (no npm deps) for
  screenshots + layout measurement. Attach to the **page** target from
  `http://127.0.0.1:9222/json` (the `/json/version` browser URL has no
  `Target`/`Page` domains enabled).
- **Port 12000** is the work-host preview port; a stale `tapejudge.web.app`
  process can hold it — kill the specific PID, not a broad `pkill -f python`.
- `create_app()` binds a new `Store` per call; the live server uses a file DB at
  `TAPEJUDGE_DB`. A fresh server needs seeding before screenshots show data.

## Mission 5 (2026-11-07) — review + fixes, commit `b1c9338`

15 defects fixed (see `docs/TAPEJUDGE_VULNERABILITY_REGISTER.md`). Key ones:
read-only pages 500'd on no-rubric / malformed stored rows; `int(True)`/`int(5.9)`
silently coerced scores; panel size could disagree with rubric judges; a
check-then-insert race surfaced a raw `sqlite3.IntegrityError`; no security
headers/cookie flags and a fixed default `SECRET_KEY`; no rate limiting; real
horizontal scroll at 390px (up to 97px) from grid/flex `min-width:auto`.

- Suite grew **109 → 135** (`tests/test_hardening.py`, 26 tests). Live-chain
  tests pass against X Layer when enabled.
- `service._as_int` is the one whole-number parser (rejects bool/float).
- Conflicts are returned as **400** (not 409) for `/api/*` to keep the existing
  contract; HTML conflicts are 409. Intentional, documented.
- Rate limiter is **in-process** (`web/ratelimit.py`); fine for one instance.

## Release gate (recorded decision)

**Mission 6 (2026-11-07) closed the blocker.** Authentication + object-level
authorisation now gate every write route; public verification stays anonymous.
Verdict: **ready for a public release of the full surface**, conditional on the
deployment plan (TLS, `TAPEJUDGE_SECRET`, shared store/limiter only if >1
instance). Residual is fairness, not exposure: the app proves *which account*
acted, not whether the judge was fair.

## Mission 6 (2026-11-07) — auth + authorisation

- `tapejudge/auth.py` = mechanism (scrypt, session rotation, `session_epoch`,
  CSRF, `LoginThrottle`); `service.py` = policy (`require_organiser`, judge
  membership binding, viewer-scoped entries); `web/app.py` = enforcement (central
  CSRF `before_request`, central `AuthError` handler). Identity comes **only**
  from the session; a body-supplied role/label/owner is never read.
- **Uniform refusal invariant:** `AuthError` must reach the central handler, so
  the ownership gate is called **outside** every route's `try`. Calling it inside
  swallowed it into a flash → a 200 for a foreign competition vs 404 for unknown =
  an existence oracle (V-16). Fixed on `rubric`/`publish`/`results` (`judges` was
  already correct). Keep new organiser routes matching `judges`.
- Suite **135 → 196** (`tests/test_auth.py` 59 tests + `tests/authtestkit.py`).
  `python3 -m unittest discover -s tests` → 196 OK; ruff clean; pip-audit clean.
- Docs: new `docs/MISSION6_SECURITY_REVIEW.md`; THREAT_MODEL/VULN_REGISTER (V-16,
  A-01 closed)/RELEASE_READINESS/REGRESSION_EVIDENCE/DEPLOYMENT_PLAN/README
  updated. ARCHITECTURE.md gained an "Authentication and authorisation" section.
- Auth-suite gotcha: helpers must fetch CSRF from a **200** page (a `/` or the
  target page); fetching a token from a 404 raises before the request is sent.
