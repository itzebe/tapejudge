# TapeJudge — Mission 8: public launch and hackathon readiness

Research/execution date: **2026-11-07**. Baseline `1e9bd88`; this pass is
`d10c4c1` (and the documentation commit that follows it). This report states what
was executed, what was verified live, what is blocked and why.

---

## 1. Outcome in one line

TapeJudge is **publicly deployed and running** on a durable store, with the full
workflow verified live end to end; the durable Postgres backend and a launcher
were added and committed; the offline demo was fixed and given a regression test;
and the remaining blockers are named honestly (a managed Postgres slot and the
inherent signing boundary).

---

## 2. Public deployment

| | |
|---|---|
| URL | `https://work-1-gvcyygdjrlurcmdz.prod-runtime.all-hands.dev/` |
| Runtime | gunicorn, 1 worker (SQLite is single-writer), bound `0.0.0.0:12000` |
| Store | SQLite on a **persistent** path: `/workspace/.tapejudge-data/tapejudge.db` |
| Secret | generated once, stored at `/workspace/.tapejudge-data/secret` (mode 600) |
| Launcher | `scripts/serve_public.sh` (committed) |

The launcher enforces the same production invariants the container entrypoint
does: it refuses to start if `TAPEJUDGE_ALLOW_DEV_SECRET` is set, generates a
strong `TAPEJUDGE_SECRET` when one is not provided (and persists it so sessions
survive a restart), seeds the SYNTHETIC demo once, and serves through gunicorn.

**Durability was proven, not assumed:** the app was restarted and the two
competitions in the database were still present afterwards.

---

## 3. Live verification (what was actually run)

### 3.1 End-to-end workflow

`node scripts/journey_probe.mjs <public-url>` — **13/13 checks pass**: sign up,
create a competition, freeze the rubric, invite a judge, submit an entry, score
it, settle, and verify publicly. It also asserts the security properties live:

- a write without a CSRF token is refused (HTTP 403);
- public `/verify` and `/results` are reachable anonymously (HTTP 200);
- `/verify` reproduces the ranking from the frozen rule
  (`netlist_hex_matches`, `netlist_sha256_matches`, `rubric_digest_matches`).

### 3.2 Security headers and abuse handling (live)

| Probe | Result |
|---|---|
| `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` | present |
| `/api/*` `Cache-Control` | `no-store` |
| Anonymous write (`POST /api/competitions`) | `401` |
| Oversized body (> 256 KB) | `413` |
| Malformed JSON body | `401` (auth is checked first) |
| All eight screens + `/api/*` | no `5xx` |

### 3.3 Accessibility and layout (live, headless Chromium over CDP)

`node scripts/a11y_audit.mjs` on `/`, `/competitions`, `/signin`, `/signup`,
`/join` at **390 / 768 / 1440 px** — **15/15 pages clean**: 0 horizontal
overflow, exactly one `h1` per page, no heading skips, 0 sub-44px non-prose tap
targets, 0 low-contrast text, 0 images without `alt`, 0 unnamed controls.

`node scripts/visual_qa.mjs` rendered `/`, `/c/<id>/results` and
`/c/<id>/verify` at all three widths with **0 console errors** and 0 unnamed
controls. Screenshots were produced (kept in `/tmp/shots/`; not committed, as
they are build artefacts).

### 3.4 Quality gates

| Gate | Command | Result |
|---|---|---|
| Lint | `python3 -m ruff check .` | **clean** |
| Unit + web + integration | `python3 -m unittest discover -s tests` | **237 passed**, 8 skipped (the live-chain gates) |
| Postgres parity | `TAPEJUDGE_TEST_DATABASE_URL=… python3 -m unittest tests.test_store_contract` | **16 passed** (same contract as SQLite) |
| Dependency audit | `python3 -m pip_audit` | **no known vulnerabilities** |

---

## 4. What changed in this pass

| File | Change |
|---|---|
| `tapejudge/store_pg.py` (new) | `PostgresStore` — the same `Store` interface over Postgres, with an optional schema for isolation. |
| `tapejudge/store.py` | `make_store()` selects Postgres from `TAPEJUDGE_DATABASE_URL`, else SQLite. |
| `tapejudge/web/app.py`, `tapejudge/seed.py` | Use `make_store`. |
| `tests/test_store_contract.py` (new) | One behavioural contract, run against both backends. |
| `tests/test_demo.py` (new) | Runs the offline demo end to end and asserts its honest outcomes. |
| `demo/run_demo.py` | Fixed a rotted stub: `prepare_tapeout()` now accepts `n_gates` and the stub implements `simulate_tapeout()`. |
| `scripts/serve_public.sh` (new) | Single-instance production launcher. |
| `tapejudge/web/templates/base.html` | Open Graph / Twitter preview tags (no image claimed without an asset). |
| `tests/test_hardening.py` | Backend-aware tamper helper; pins the preview metadata. |
| `pyproject.toml`, `.env.example`, `README.md`, `docs/TAPEJUDGE_PRODUCTION_DEPLOYMENT_PLAN.md` | Document the durable store and the single-instance host. |
| `evidence/chain_evidence.json` | Re-read live (read-only); block advanced, publication still honestly `not sent`. |

The demo fix matters: `demo/run_demo.py` is the first thing a newcomer runs, and
it was crashing at the publication step because nothing tested it. It now has a
regression test.

---

## 5. X Layer integration revalidation (read-only)

Re-ran `python scripts/capture_chain_evidence.py` against `https://rpc.xlayer.tech`
(chain id 196). The block advanced (75,322,480 → 75,326,296) and every other
fact was unchanged: the processor registry still answers `cpuCount()`, the
circuit beacon is still ownerless, and the read-only `eth_call` simulation of the
real `tapeout` still returns `needs_transistors` (a signed transaction that burns
transistor tokens is required — the app correctly stops at the unsigned boundary
and never claims an on-chain circuit it did not create).

---

## 6. Blockers, stated honestly

1. **A managed Postgres slot could not be provisioned.** The Supabase free tier
   caps a user at **2 active projects**, and both are used by unrelated work. A
   new organisation was created, but the account role cannot create projects in
   it, and creating projects in the user's existing organisations would require
   deleting their other projects — which was **not** done. Vercel's
   Supabase integration is installed but reports "Installation does not support
   provisioning". **Mitigation:** the deployment runs on a **single instance with
   a persistent disk**, where SQLite is genuinely durable, and the Postgres
   backend is implemented and tested so a managed DSN can be dropped in with one
   environment variable when a slot is available.

2. **Publication is prepared, not executed.** This is a product decision, not a
   gap: the app holds no key. Wiring a signer needs a human decision about keys
   and a funded wallet holding transistor tokens. The verify page never claims an
   on-chain circuit.

3. **`main`/`master` history is shallow in the workspace clone**; the release
   commit and its parent were confirmed against the GitHub API instead.

---

## 7. How to reproduce

```bash
# Tests (offline) and lint
TAPEJUDGE_SKIP_CHAIN=1 python3 -m unittest discover -s tests
python3 -m ruff check .

# Run it on a single-instance host
TAPEJUDGE_DATA_DIR=/var/lib/tapejudge TAPEJUDGE_PORT=12000 sh scripts/serve_public.sh

# Prove the live workflow
node scripts/journey_probe.mjs https://<host>/
node scripts/a11y_audit.mjs https://<host>/ / /competitions
```

---

## 8. Remaining limitations (carried forward)

- The MVP caps a judging panel at 25 and uses integer scores.
- Fairness is not proven (by design): the app shows *which account* scored, not
  whether a judge was fair.
- A multi-instance deployment still needs a shared rate limiter (the store is
  already shareable).
- The public URL is a workspace runtime host, not a custom domain; the
  `deployment plan` documents the production topology for a real host.
