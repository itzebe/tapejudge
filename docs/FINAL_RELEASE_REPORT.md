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
| Public URL | `curl https://work-1-…prod-runtime.all-hands.dev/` | `200` |
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

- **Durable hosting.** The public URL runs inside a work-host sandbox that does
  not survive the session ending, and the workspace has no service manager
  (PID 1 is `openhands-agent`). `scripts/autostart.sh` restores the app after a
  reset within this workspace, but it cannot make the sandbox a managed host.
  A permanent host, or a managed Postgres DSN (`TAPEJUDGE_DATABASE_URL`), is
  still required for a durable public deployment. The free managed-Postgres slot
  was unavailable (the workspace Supabase projects are at their free-project
  cap), so this is a credential/plan blocker, not a code gap.
- **Publication is prepared, not sent.** The app holds no key by design; a
  circuit ID exists only after the operator signs and broadcasts the prepared
  transaction, and the app then confirms it by reading the chain.
- **Fairness is not proven.** The product fixes the *rule* and makes the
  *arithmetic* reproducible; it does not prove a judge was fair.

---

## 5. Remaining limitations

- The demo database currently holds one competition, `SYNTHETIC — Demo Judging
  Round` (`comp_3801e585d939`); the journey-probe debris was removed so the
  public demo shows only the labelled demo round.
- The `tapeout` transaction was not broadcast; `PUBLISH_ONCHAIN_VERIFIED` is set
  only by a real match against chain state, which has not happened.
