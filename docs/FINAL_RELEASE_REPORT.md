# TapeJudge — final release report

Final hardening pass, **2026-11-08**. Baseline `2afc31a` (HEAD at the start of
this pass); this report describes what was changed, what was verified live, and
what remains blocked. Every claim below was checked against the running app on
this date; nothing is asserted that was not executed.

---

## 1. Outcome in one line

TapeJudge is publicly deployed, the TapeOut integration now **confirms** a real
publication instead of only preparing one, the security and accessibility
posture was re-verified live, and the submission kit, demo script and release
evidence are complete — with the one honest blocker (durable hosting) named.

---

## 2. What changed in this pass

| Change | Files | Why |
|---|---|---|
| Confirm a real tapeout on chain | `tapejudge/chain.py`, `tapejudge/xlayer.py`, `tapejudge/service.py`, `tapejudge/web/app.py`, `tapejudge/web/templates/publish.html`, `tests/test_publication.py` | The app prepared an unsigned `tapeout` but never checked whether it landed. `verify_circuit_against_chain` reads the circuit back and compares it byte-for-byte with the frozen netlist; the service only marks a competition `PUBLISH_ONCHAIN_VERIFIED` when the netlist matches. |
| Human-readable publication package | `scripts/prepare_publication.py` | Prints the exact unsigned transaction, a calldata-only gas floor, and the `cast` commands to send and confirm it. Holds no key. |
| Favicon 404 fixed | `tapejudge/web/app.py`, `tests/test_web.py` | Browsers request `/favicon.ico` on every page; it was a logged 404. Now served from the declared SVG. |
| Mobile tap target fixed | `tapejudge/web/templates/publish.html` | The tx-hash field had no `type`, so the input styling (and its 40px min-height) never applied — a 22px target on mobile. |
| Auto-start after reset | `scripts/autostart.sh`, `~/.bashrc` | The work-host runtime does not survive a session ending; a guarded, idempotent start restores the demo after a reset. |
| Submission kit + demo script | `docs/HACKATHON_SUBMISSION.md`, `docs/FINAL_DEMO_SCRIPT.md` | Maps the project to the official requirements read from the event page. |

---

## 3. Live verification (commands and results)

Run against the public deployment on 2026-11-08.

| Check | Command (abridged) | Result |
|---|---|---|
| Health | `curl /api/health` | `{"ok": true}` |
| Smoke: pages | `curl /`, `/c/<id>/publish`, `/c/<id>/verify`, `/api/integration` | all `200` |
| Public URL | `curl https://tapejudge.vercel.app/` | `200` |
| Favicon | `curl -o /dev/null -w '%{http_code} %{content_type}' /favicon.ico` | `200 image/svg+xml` |
| Served asset == repo | sha256 of `style.css` (repo) vs served | identical |
| Full test suite | `python3 -m unittest discover -s tests` | **246 OK** (skipped=8) |
| Publication tests | `python3 -m unittest tests.test_publication` | **7 OK** |
| Lint | `python3 -m ruff check .` | All checks passed |
| Journey probe | `node scripts/journey_probe.mjs http://127.0.0.1:12000` | **13/13** |
| Accessibility | `node scripts/a11y_audit.mjs …` at 390/768/1440 | 0 overflow, 0 small targets, 0 low-contrast |
| Offline demo | `python demo/run_demo.py` | completes; "frozen rule intact: True"; "No transaction was sent." |

Security controls, re-verified live:

| Control | Probe | Result |
|---|---|---|
| CSRF on writes | `POST /api/competitions` with no token | `401` (anonymous), `403` when authenticated but tokenless |
| Anonymous write refused | `POST /c/<id>/publish` with no session | `302 → /login` (no state change) |
| Login throttle | 12 failed logins, same ip+email | bounded by `LoginThrottle` (limit 10 / 300 s) |
| Security headers | `curl -D -` | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, scoped CSP, `frame-ancestors 'none'` |
| API no-store | `curl -D - /api/integration` | `Cache-Control: no-store` |
| HSTS | default | absent unless `TAPEJUDGE_ENABLE_HSTS=1` (correct: no HSTS on plain-HTTP local) |

---

## 4. Honest blockers

- ~~**Durable hosting.**~~ **Resolved in the release-closure pass** — see §6.
  The app now runs on Vercel (`tapejudge.vercel.app`) backed by a Neon Postgres
  store, so records survive a cold start. The earlier work-host URL was a
  sandbox and is no longer the access link.
- **Publication is prepared, not sent.** The app holds no key by design; a
  circuit ID exists only after the operator signs and broadcasts the prepared
  transaction, and the app then confirms it by reading the chain.
- **Fairness is not proven.** The product fixes the *rule* and makes the
  *arithmetic* reproducible; it does not prove a judge was fair.

---

## 5. Remaining limitations

- The demo database currently holds one competition, `SYNTHETIC — Demo Judging
  Round` (`comp_563ebff5be18`); the journey-probe debris was removed so the
  public demo shows only the labelled demo round.
- The `tapeout` transaction was not broadcast; `PUBLISH_ONCHAIN_VERIFIED` is set
  only by a real match against chain state, which has not happened.

---

## 6. Release-closure pass (2026-11-08, second pass)

A second pass closed the outstanding release-management work and converged the
project on **one verified, consistent production release**. Baseline for the
pass was `172d040`; everything below was executed against the live deployment on
this date.

### 6.1 Durable public deployment

The app is deployed on Vercel (project `tapejudge`, team `marvinsjones06-1839`)
at <https://tapejudge.vercel.app>, backed by a free **Neon Postgres** database
provisioned through the Vercel marketplace. Git auto-deploy is connected, so
`master` deploys on push.

| Check | Command | Result |
|---|---|---|
| Health | `curl https://tapejudge.vercel.app/api/health` | `{"chain_reachable":true,"ok":true}` |
| Landing | `curl -o /dev/null -w '%{http_code}' https://tapejudge.vercel.app/` | `200` |
| Demo ranking | `curl /c/comp_563ebff5be18/results` | `200` |
| Demo verification | `curl /c/comp_563ebff5be18/verify` | `200` |
| Integration | `curl /api/integration` | `state: PREPARED_BUT_AWAITING_AUTHORISATION`, `reads_verified_live: true` |

The store is selected by `TAPEJUDGE_DATABASE_URL` with a **stable** schema
(`TAPEJUDGE_DB_SCHEMA`, hashed to `t_…`), so every serverless instance reads the
same database; `create_app` refuses to boot on Vercel without a fixed secret, a
DSN and a schema.

### 6.2 Production store hygiene

The end-to-end probe (`scripts/journey_probe.mjs`) creates a real competition
each run. Five runs against the deployment had left five `Journey Probe Round`
rows on the public landing page. Two maintenance tools were added, each with a
pure, tested core:

- `scripts/purge_demo_competitions.py` — removes competitions by **exact name**
  and every row that references them, in one transaction; dry-run by default;
  refuses to delete every competition. Five probe rounds were removed
  (5 competitions, 5 entries, 5 evaluations, 5 invitations, 15 memberships, 35
  audit events).
- `scripts/reset_production_store.py` — converges the store on an explicit
  keep-list (the demo competition and the three demo accounts), so a reset is a
  reviewable baseline, never "delete whatever looks like junk". Used to remove
  235 fixture competitions and 10 fixture accounts that a mis-scoped local test
  run had written to the production schema (see 6.3).

The landing page now lists exactly one competition — the labelled
`SYNTHETIC — Demo Judging Round`.

### 6.3 Test-isolation guard (root-cause fix)

The incident behind 6.2 was possible because `create_app(testing=True)` adopted
whatever `TAPEJUDGE_DATABASE_URL` / `TAPEJUDGE_DB_SCHEMA` the shell carried, so a
test run in a deployed shell wrote into the production schema. `create_app` now
calls `_require_test_isolation`: a testing app **refuses to start** when
`TAPEJUDGE_DATABASE_URL` is set. Tests use the SQLite store; the Postgres path is
exercised by `tests/test_store_contract.py` against an isolated
`TAPEJUDGE_TEST_DATABASE_URL` schema. Pinned by
`tests/test_deployment.py::test_testing_app_refuses_the_production_database`.

`scripts/journey_probe.mjs` also warns loudly when its target is not local, so
the debris cannot accumulate unnoticed again.

### 6.4 Interface: editorial design system

The interface was rebuilt as a **warm editorial ledger** (see
`docs/DESIGN_SYSTEM.md`): deep-ink sections against warm ivory, a single rust
accent that means "the fixed thing", Fraunces for statements, a humanist sans
for controls and mono for all evidence. The landing page now argues what
TapeJudge is and why a frozen rule matters; the honesty model (frozen /
prepared / published / verified) is rendered as a state rail; every template was
moved onto the shared component set. Fonts are self-hosted (latin + latin-ext,
~280 KB) so no third-party origin is added to the CSP.

### 6.5 Verification (release-closure pass)

| Check | Command | Result |
|---|---|---|
| Lint | `python3 -m ruff check .` | All checks passed |
| Full suite (offline) | `TAPEJUDGE_SKIP_CHAIN=1 python3 -m unittest discover -s tests` | **263 OK** (skipped=16) |
| Journey probe | `node scripts/journey_probe.mjs http://127.0.0.1:12010` | **13/13** |
| Accessibility | `node scripts/a11y_audit.mjs … / /login /signup /new /c/…/results /c/…/verify` at 390/768/1440 | **0 failing / 18 checks** |
| Production landing | `curl https://tapejudge.vercel.app/` | 1 competition (demo only) |

### 6.6 Documentation corrected

- `docs/HACKATHON_SUBMISSION.md` — the access link and the hosting note now
  describe the durable Vercel deployment, not the sandbox.
- `docs/FINAL_DEMO_SCRIPT.md` — the demo competition id is `comp_563ebff5be18`
  (the one that exists in production), and the tab list uses real routes
  (`/results` is the public hub; there is no bare `/c/<id>` overview route).
