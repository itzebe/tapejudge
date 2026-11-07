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

Ready to prepare for a **restricted** public release; **not** ready for an
unrestricted one until **authentication + object-level authorisation** gate the
write routes. The blocker is exposure, not integrity — the integrity promise
held under adversarial testing.
