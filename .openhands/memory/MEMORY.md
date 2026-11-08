# TapeJudge — project memory

Agent-written notes about `/workspace/tapejudge`. Verify before relying on them.

## What it is

Standalone Flask + SQLite MVP: a judged-competition scoring app whose scoring
rule is compiled to a TapeOut circuit and frozen, and whose ranking is
recomputed from stored inputs on every read (no stored result). Vendored Mission
3 core (`netlist/rubric/engine/verifier/lifecycle/chain`) is byte-faithful — do
not reformat. Git remote **`https://github.com/itzebe/tapejudge`** (default
`master`); no `main` branch.

## Controlled release — 2026-11-08 (FINAL; this is the current release state)

- **One consistent release across GitHub + Vercel.** `master` HEAD **`264297c`**
  (PR #4, release-closure pass; earlier HEAD `5b3d98d` after PR #2; the deployment
  implementation merge is `bb8bc66`, PR #1). Production is aliased to
  https://tapejudge.vercel.app. Evidence: `docs/CONTROLLED_RELEASE_EVIDENCE.md`
  and `docs/FINAL_RELEASE_REPORT.md` §6 (release-closure pass).
- **A test build can no longer open a hosted DB — it fails closed.** `create_app(testing=True)`
  refuses to start when **any** of eight hosted-DB indicators is present
  (`_HOSTED_DB_INDICATORS` in `tapejudge/web/app.py`: `VERCEL`, `TAPEJUDGE_DATABASE_URL`,
  `DATABASE_URL`, `POSTGRES_URL`, `POSTGRES_URL_NON_POOLING`, `POSTGRES_PRISMA_URL`,
  `POSTGRES_HOST`, `POSTGRES_USER`), before `make_store()` is reached. It refuses
  rather than falling back to SQLite, so a misconfigured test host cannot look green.
  Deliberately excluded: `TAPEJUDGE_DB` (a SQLite path) and `TAPEJUDGE_TEST_DATABASE_URL`
  (the isolated contract DSN, which never flows through `create_app`). Contract:
  `docs/ARCHITECTURE.md` §"Test database isolation".
- **PRs created via the API land as DRAFTS.** `gh pr ready <n>` (or the GraphQL
  `markPullRequestReadyForReview` mutation) is required before merge; the REST
  ready endpoint 404s for this token. Applies to PR #1 and PR #4.
- **Auto-deploy IS enabled** (`createDeployments: enabled`): merging to `master`
  produced a production deployment at the merge commit automatically (`source: git`).
  Do not assume it is off; the earlier "disabled" reading was wrong.
- **Secret rotated:** `TAPEJUDGE_SECRET` (shared prod+preview) was rotated in place
  with a fresh 256-bit value (value never printed; passed inside a script). It
  signs the session cookie (which carries the CSRF token), so rotation logs
  everyone out — no lockout, password hashes are unaffected. **A rotation needs a
  redeploy to take effect.** Redeploy API: `POST /v13/deployments?forceNew=1`
  with `{"deploymentId": <old>, "target":"production", "name":"tapejudge"}`
  (the `/v10/.../redeploy` and `/v13/.../redeploy` paths 404).
- **Postgres test debris:** each test store makes a random `t_<hex>` schema; the
  suite left 78 on Neon. Dropped 77, kept production **`t_d69196def4cf`** (holds
  exactly the 3 live competitions). Do NOT set `TAPEJUDGE_DB_SCHEMA` when running
  the suite — it forces all stores onto one schema and causes spurious failures.
- **README overclaim fixed (PR #2):** the tagline said the rule "is published …
  through TapeOut"; nothing is published by default and the UI says a local
  circuit is not on-chain. Now "frozen as a TapeOut circuit". The UI template
  gates "published on X Layer" behind `{% if c.publication.on_chain %}` — correct.
- **Env reset wipes the Python env + the Vercel CLI.** After a sandbox reset:
  `python3 -m pip install -e '.[dev]' pip-audit` and `npm install vercel
  --no-save --prefix /tmp/verceltool` (global `npm i -g` fails on permissions).
  `.env.local` (Neon DSNs) survives.
- **Known gaps:** `/robots.txt` and `/sitemap.xml` 404; no on-chain circuit exists
  (by design — the organiser's wallet must sign a funded `tapeout`).

## Production deployment (Vercel + Neon) — 2026-11-08, PR #1

- **Live:** https://tapejudge.vercel.app. Vercel project `tapejudge`
  (`prj_c6Nvlf262Z1B0OQfe5Tiaw6mf2xT`), team `team_wxFCzsPoY7GBXnRvV1fIwUsz`
  (slug `marvinsjones06-1839`). Git auto-deploy connected.
- **DB:** a **free Neon Postgres** provisioned through the Vercel marketplace
  (`vercel integration accept-terms neon --yes`, then `vercel integration add
  neon`). The Supabase org is at its 2-active-free-project cap, so Neon was the
  unblock. Use the **unpooled** endpoint (`DATABASE_URL_UNPOOLED`): the store
  keeps one connection per instance and sets a session-level `search_path`,
  which a transaction-mode PgBouncer pooler does not preserve.
- **Env (production + preview):** `TAPEJUDGE_SECRET` (fixed 32-hex),
  `TAPEJUDGE_DATABASE_URL` (unpooled DSN), `TAPEJUDGE_DB_SCHEMA`
  (`tapejudge-prod` / `tapejudge-preview`), `TAPEJUDGE_ENABLE_HSTS=1`.
- **THE bug that cost the first deploy:** Vercel installs **only
  `[project.dependencies]` and ignores an optional extra**. `psycopg` in the
  `server` extra was never installed → `ModuleNotFoundError: No module named
  'psycopg'` → `FUNCTION_INVOCATION_FAILED` on every request (build log said
  "Installing required dependencies from pyproject.toml", no error). Fixed by
  promoting `psycopg[binary]>=3.2` to a base dependency; `tests/test_deployment.py`
  pins it. A `requirements.txt` is NOT read when `pyproject.toml` exists.
- **Serverless store rule:** each request builds a new store instance, so the
  Postgres schema must be **stable** (`TAPEJUDGE_DB_SCHEMA`); a random schema
  reads an empty database. `create_app` refuses to boot when `VERCEL` is set and
  the secret/DSN/schema is missing (never a random secret, never `:memory:`).
- **Deploy command:** `vercel deploy --prod --yes --token "$VERCEL_TOKEN"
  --scope team_wxFCzsPoY7GBXnRvV1fIwUsz` (CLI at `~/.npm-global/bin`).
- **Verify a deploy:** `/api/health` → `{"chain_reachable":true,"ok":true}`;
  `node scripts/journey_probe.mjs https://tapejudge.vercel.app` → 13/13;
  `node scripts/a11y_audit.mjs <url> / /login /new` → `failing: 0`.

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

## Mission 7 (2026-11-07) — release review, deployment, final gate

Repo is a local-only git repo (`master`); HEAD `cb46979`. Suite **196 → 219**
(+`tests/test_deployment.py`, +Mission-7 regressions). Final gate: full suite
**219 OK / 0 skipped** (network), 8 skipped offline; `ruff` clean; `pip-audit`
"no known vulnerabilities"; rendered sweep **21/21** (7 pages × 390/768/1440).

Three release-surface defects found and fixed (register V-17…V-19):
- **V-17 PII leak:** public unauthenticated `/api/competitions/<cid>/audit` served
  a judge's email in the `judge_invited` payload. Fix: stop recording it AND
  `store.redact_event_payload()` (top-level `_REDACTED_EVENT_KEYS={"email"}`)
  applied in `list_events` so pre-existing rows are redacted too. NOTE: `store.
  list_events` is the *only* path with that `sort_keys` signature; the other
  `list_events`-named function is `engine.audit_trail` (different module).
- **V-18 open redirect:** `next=` used a bare `startswith("/")`; a browser treats
  `\` as `/`, so `next=/\evil.com` left the site. Fix: `web/app.py::_safe_next`
  (single leading slash, no backslash, no control char).
- **V-19:** checkbox was 13×13; now 24×24 (`style.css`, `:has()` flex label).

Deployment is verified in two places: Docker isn't available in this container
session, so the **production path was exercised directly** — `sh scripts/
docker-entrypoint.sh` (needs `gunicorn` on PATH; it lives in `~/.local/bin`) +
gunicorn → `/api/health` 200, security headers present, journey 13/13. The
entrypoint hard-refuses to start without `TAPEJUDGE_SECRET` or with
`TAPEJUDGE_ALLOW_DEV_SECRET` set. `.env.example` now documents `TAPEJUDGE_WORKERS`.

- Work-host preview is a **dev** server (`python -m tapejudge.web.app`) on 12000,
  seeded from `/tmp/tjdemo/app.db`; needs `TAPEJUDGE_ALLOW_DEV_SECRET=1
  TAPEJUDGE_INSECURE_COOKIES=1`. Seeding the demo file with BOTH `python -m
  tapejudge.seed <db>` and `--journey` gives 2 competitions (one ready for the
  journey probe's accounts).
- Live X Layer reads confirmed this session: chain 196, block ~75.32M, 415
  processors on the factory, `next_id` 103; the app's local TAP-02 evaluator
  reproduces on-chain `eval` byte-for-byte for circuits 1–3 (all input combos).
  Sequential circuits (nState>0) must use `step_circuit`, not `verify_circuit_
  against_chain`. `XLayerClient` has no `next_circuit_id` — it is `next_id()`.
- Added an MIT `LICENSE`. Did **not** create a GitHub remote (not explicitly
  authorised); the repo is self-contained and independently runnable.

## Mission 8 (2026-11-07) — public launch, durable store, hackathon readiness

Repo now has a **remote**: `https://github.com/itzebe/tapejudge` (**PUBLIC**,
default `master`). HEAD `730e97c`. Push with an ephemeral token URL
(`git push "https://${GITHUB_TOKEN}@github.com/itzebe/tapejudge.git" master:master`)
— the `origin` remote is deliberately token-free, so a bare `git push` hangs on a
username prompt.

- **Public deployment:** `https://work-1-gvcyygdjrlurcmdz.prod-runtime.all-hands.dev/`
  (→ 200). gunicorn 26.2.0, 1 worker, on `0.0.0.0:12000`, launched by
  `scripts/serve_public.sh` (new, committed). Durable SQLite at
  `/workspace/.tapejudge-data/tapejudge.db` on the persistent workspace disk;
  secret persisted at `/workspace/.tapejudge-data/secret` (600) so sessions
  survive a restart. **Durability proven** by restarting and finding both
  competitions still present.
- **Postgres store added:** `tapejudge/store_pg.py` (`PostgresStore`, same `Store`
  interface + `DuplicateError`, optional schema isolation); `store.py::make_store()`
  picks it when `TAPEJUDGE_DATABASE_URL` is set. `tests/test_store_contract.py`
  runs the **same** behavioural contract on both backends. Full suite is green on
  both: `TAPEJUDGE_TEST_DATABASE_URL=<dsn> python3 -m unittest discover -s tests`
  → **237 OK** (also 237 OK on SQLite). Driver is the `server` extra
  (`pip install -e '.[server]'`, psycopg[binary]>=3.2).
- **Supabase/Vercel provisioning is hard-blocked (do not retry blindly):**
  Supabase free tier caps **2 active projects per user** and both are unrelated
  work; a new org `tapejudge-org` (`wkfthwnxmsabynflprkk`) was created but the
  account role cannot create projects in it. Vercel's Supabase integration
  reports "Installation does not support provisioning". No project was deleted.
  → single-instance persistent-disk SQLite is the deployment; the Postgres
  adapter is ready for a managed DSN when a slot exists.
- **`demo/run_demo.py` had rotted** (crashed at publication): its `_NoChain` stub
  `prepare_tapeout()` lacked the `n_gates` kwarg the service passes, and had no
  `simulate_tapeout()`. Nothing tested the demo. Fixed + `tests/test_demo.py`
  runs it end to end and asserts the honest outcomes. **If you change the chain
  client interface, update the demo stub too.**
- `base.html` gained Open Graph / Twitter card tags (self.title()/self.description()
  so child pages populate previews; no image tag without an asset). Pinned by
  `tests/test_hardening.py::test_social_preview_metadata_present`.
- Live gates re-run this session: journey probe **13/13**; a11y **15/15** at
  390/768/1440; security headers present (**HSTS still absent** — the work-host
  runtime fronts TLS); `/api/*` no-store; anon write 401; oversized 413; ruff
  clean; pip-audit clean. X Layer evidence re-read read-only (block advanced to
  ~75.33M; publication still honestly `needs_transistors`/not sent).
- Docs added: `docs/MISSION8_PUBLIC_LAUNCH_REPORT.md`,
  `docs/HACKATHON_SUBMISSION.md`; README + deployment plan updated for the
  single-instance host and the two stores.

## Final hardening pass (2026-11-08)
- **Publication confirmation closed the last real integration gap.** Before this,
  `PUBLISH_ONCHAIN_VERIFIED` was never set. Now `verify_circuit_against_chain`
  (`xlayer.py`) reads `netlist(id)` back and compares byte-for-byte with the
  frozen netlist; the service marks published only on a match. `get_receipt` /
  `get_transaction_receipt` added. Confirm route + form in `publish.html`.
  `tests/test_publication.py` (7). Also `scripts/prepare_publication.py` prints
  the unsigned tx + `cast` commands.
- **`tapeout` selector is `0x7bd3ac1d`**, pinned by
  `assert selector("tapeout(bytes,uint32,uint32)") == TAPEOUT_SELECTOR` in
  `xlayer.py`. Do not trust a selector typed from memory (I wrote `0x09c117e2`
  first and had to correct it against the code + the calldata prefix).
- **Browser favicon 404:** Chrome requests `/favicon.ico` even with an SVG icon
  declared; added an explicit route serving the SVG. `test_web.py` pins it.
- **Tap-target footgun:** an `<input>` with no `type` gets no input styling, so
  its min-height never applies (22px target). Always set `type="text"`.
- **Autostart mitigation:** the sandbox has no service manager (PID 1 is
  `openhands-agent`); `scripts/autostart.sh` (idempotent, non-blocking, no-op if
  already up) is invoked from `~/.bashrc` so the demo returns after a reset. It
  cannot make the sandbox a managed host.
- Final gates: suite **246 OK** (skipped=8), journey probe 13/13, a11y 0/0/0 at
  390/768/1440, ruff clean, public URL 200s and serves the new code.
- Deliverables: `docs/HACKATHON_SUBMISSION.md` (rewritten to the official
  requirements read 2026-11-08), `docs/FINAL_DEMO_SCRIPT.md`,
  `docs/FINAL_HACKATHON_EVIDENCE.md`, `docs/FINAL_RELEASE_REPORT.md`,
  `evidence/screenshots/`.

## Demo video (2026-11-08, branch `feat/final-demo-video` @ `f0a6a94`)
- **Deliverable:** `demo/tapejudge-final-demo.mp4` — **2:15**, 1280×712 H.264 +
  AAC. Recorded on a **local instance** of the real app, not production. The
  authoritative doc is `docs/FINAL_DEMO_VIDEO.md` (recorded shot list, verbatim
  narration, honesty notes, reproduction). `docs/FINAL_DEMO_SCRIPT.md` is now
  only the planning script and points at it.
- **Baseline:** `origin/master` = **`5d62342`** ("Fail-closed test-DB isolation…
  (#5)"). The local `master` ref here was `4bf0d3d` and **stale** — always fetch
  and branch from `origin/master`. `harden/test-db-isolation` diverged.
- **Tool:** `scripts/demo_video.py` (dev-only; not a project dependency).
  Commands: `seed` (isolated synthetic DB via the real service), `narrate`
  (Piper), `record` (Playwright + `ffmpeg -f x11grab` on Xvfb `:99`), `assemble`,
  `verify`. Only `demo/…mp4` + `docs/…md` + `evidence/final-demo-scenes/` are
  repo artifacts; everything else lives in `$TMPDIR/tapejudge-demo`.
- **Environment quirks that cost time:**
  - Chromium `--app=` **did not** hide its toolbar here, so every frame is
    cropped to the page: `crop=1280:712:0:88` (top 88px is chrome). Do not assume
    `--app` is chromeless.
  - `page.evaluate` cannot see Python-local helpers; pass them as args
    (`([k,t,s]) => …`, `[kicker,title,sub]`).
  - `input[name=crit_weight]` is type=number; `fill("3")` is correct (not
    `fill(3)`).
  - `.first` on a Playwright locator is a **property** (`loc.first`), not a call.
- **Verification:** `verify` checks each scene's frame luma std (not blank) and
  per-scene audio (not silent); all 9 scenes pass, `PROBLEMS: none`. Scene
  content OCR'd (frozen notice, "nothing has been sent", ranking 139/114/105,
  "The frozen rule is intact"). Suite 258 passed / 8 skipped, ruff clean.
- **Honesty model held:** synthetic data labelled `SYNTHETIC`, isolated local DB,
  no wallet, no key, no transaction; the publish scene stops at
  `onchain_pending_signature` and says "nothing has been sent". The processor
  address / OKB fee on the X Layer card are read live from chain 196.
- **Not pushed / no PR** — awaiting explicit instruction.
