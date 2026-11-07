# TapeJudge — Release Readiness Assessment

Mission 5 (independent adversarial review) with the Mission 6 authentication and
authorisation work applied. Research date **2026-11-07**. Baseline `f3c229a`; the
reviewed working tree includes the hardening in
`TAPEJUDGE_VULNERABILITY_REGISTER.md` and the identity layer in
`MISSION6_SECURITY_REVIEW.md`.

This is the release gate. It states one verdict and the reasoning behind it,
against the product's real promise and the realities of a public deployment.

---

## Verdict

> **READY for a public production release of the full surface** — public
> verification, organiser-operated competitions, judge scoring and entry
> submission — **conditional on the deployment plan in
> `TAPEJUDGE_PRODUCTION_DEPLOYMENT_PLAN.md`** (TLS, `TAPEJUDGE_SECRET`, a single
> instance or a shared store/limiter).

The Mission 5 blocking gate — **no authentication on the write routes** — is
closed. Every write now requires an authenticated session and a CSRF token;
judges score only under the membership bound to their account; organiser actions
are object-scoped; and public verification remains anonymous and free, which is
the product's promise.

---

## 1. What the gate is measured against

The product promise: *the scoring rule is fixed and public, and the ranking is
recomputable by anyone.* A release is "ready" when that promise holds under
adversarial use and when the deployment does not create new ways to break it.

Three independent axes:

| Axis | Question | Result |
|---|---|---|
| **Integrity** | Can an attacker change the rule, a score, or a result? | No observed path (§2) |
| **Availability / robustness** | Can ordinary states or bad input crash a page? | No — 0 HTTP 500s across all probes (§3) |
| **Exposure / access control** | Who can reach the write routes? | **Only authenticated, CSRF-bearing accounts acting within their role** (§4) |

---

## 2. Integrity — the product's core promise

| Property | Evidence | Verdict |
|---|---|---|
| A frozen rubric cannot be replaced | store refuses; `/verify` re-checks the digest | Holds |
| A committed score cannot change | `UNIQUE` constraint; duplicate → clean 4xx | Holds |
| A result is never stored | ranking is recomputed on every read | Holds |
| Local vs on-chain is never blurred | `publish_mode` reported verbatim; `on_chain` only for `onchain_verified` | Holds |
| A concurrent duplicate race is safe | 16-thread test: 1 row, 1 ok, 15 conflicts | Holds |
| Coercion cannot alter a score | bool/float rejected at API and domain | Holds |

All confirmed by tests **and** by a live HTTP probe. See
`TAPEJUDGE_REGRESSION_EVIDENCE.md`.

## 3. Availability and robustness

- **0 HTTP 500s** across the full probe session (server access log).
- Read-only pages render (200) in every ordinary state, including *no rubric yet*
  and a *malformed stored row*.
- Every rejected operation is a clean 4xx with a human message; oversized bodies
  are 413; wrong methods are 405.
- Security headers, hardened cookies, and a non-default secret are in place.

## 4. Exposure — the gate is closed

**Every write route now requires an authenticated session and a CSRF token.** The
Mission 5 exposure — anonymous writes — is no longer reachable:

- `POST /new`, `/c/<id>/submit`, `/c/<id>/judge`, `/c/<id>/rubric`,
  `/c/<id>/publish`, `/c/<id>/results`, `/c/<id>/judges` and the `/api/*` writes
  all refuse an anonymous caller (redirect to login, or 401/403 for the API).
- **Judge identity is bound to an account.** A judge scores only under the label
  of their membership; a label supplied in the request is ignored. Knowing a
  judge's identifier is no longer enough to submit that judge's scores.
- **Organiser actions are object-scoped.** A stranger receives the same 404 an
  unknown competition gives — byte-for-byte identical — so the response cannot be
  used to probe which competitions exist or who owns them.
- **Entrants own their entries.** A private entry is visible to its submitter and
  the organiser; everyone else sees a redacted view, including through public
  verification.
- **CSRF is enforced centrally**, so a new write route cannot be added without it.

Residuals that remain are operational, not application defects: the rate limiter
and the store are in-process/single-file, which is correct for one instance
(§5).

## 5. Deployment reality

| Item | State | Note |
|---|---|---|
| Persistence | SQLite, one file | Fine for one instance; not multi-instance |
| Rate limiting | in-process | Correct for one instance; a shared limiter is needed to scale out |
| Secret management | `TAPEJUDGE_SECRET` env var | Must be set in production (the app refuses a fixed default) |
| TLS | not terminated by the app | Required in front of it |
| Chain access | read-only `eth_call` | No key, no spend |
| Publication | prepared, not sent | Needs a funded signer — a separate, deliberate step |
| Credentials | scrypt-hashed passwords | No plaintext; login throttled |

## 6. Readiness by release shape

| Release shape | Verdict | Conditions |
|---|---|---|
| **Public read-only / verification** (anyone may view a frozen competition and reproduce it) | **Ready** | Set `TAPEJUDGE_SECRET`; TLS; single instance |
| **Organiser-operated competition with invited judges** (organiser creates, judges accept a single-use invitation and score under their account) | **Ready** | TLS; `TAPEJUDGE_SECRET`; single instance (or shared store + limiter) |
| **Unrestricted public competition** (open sign-up, self-service writes) | **Ready** | The above; a shared rate limiter and store if more than one instance runs |

## 7. Blocking items before an unrestricted public release

1. ~~**Authentication** for organisers, entrants and judges.~~ **DONE (Mission 6).**
2. ~~**Authorisation**: object-level checks on every write.~~ **DONE (Mission 6).**
3. **A shared rate limiter** if more than one instance runs.
4. **A shared store** (Postgres) if more than one instance runs.
5. **Operational**: log retention, backups of the SQLite file, and an alert on 5xx.

Items 3–5 are deployment-shape decisions, not correctness defects.

## 8. Non-blocking, recommended

- A `<meta name="robots">` policy and a `robots.txt` if the deployment should not
  be indexed.
- Structured request logging (the app currently uses Flask's default logger).
- A `Content-Security-Policy-Report-Only` pass before enforcing the strict CSP.

## 9. What changed to reach this verdict

- **Mission 5** (`TAPEJUDGE_VULNERABILITY_REGISTER.md`): 15 confirmed defects
  fixed (500s on ordinary states, score coercion, panel mismatch, a submission
  race, missing security headers/cookies/secret handling, no rate limiting, and a
  real mobile overflow), each with a regression test.
- **Mission 6** (`MISSION6_SECURITY_REVIEW.md`): authentication, judge
  memberships, object-level authorisation, CSRF, session rotation/epoch and
  viewer-scoped entry visibility; plus V-16 (the organiser-route existence
  oracle). 196 tests pass; lint and dependency audit are clean.

## 9b. Mission 7 — release review, deployment and final gate (2026-11-07)

Baseline `32533d9`. A second adversarial pass over the release surface, the
containerised production path, and the rendered interface.

- **Three further defects fixed** (`TAPEJUDGE_VULNERABILITY_REGISTER.md` V-17…V-19):
  a public-audit PII leak (a judge's email), an open redirect through `next=`, and
  a 13×13 checkbox tap target. Each has a regression test.
- **Containerised deployment** (`Dockerfile`, `scripts/docker-entrypoint.sh`,
  `docker-compose.yml`): non-root uid 10001, `/data` volume, `HEALTHCHECK` on
  `/api/health`, and a hard refusal to start without `TAPEJUDGE_SECRET` or with
  the dev-only escape set. `tests/test_deployment.py` pins the gates.
- **The production runtime path was exercised directly** (gunicorn behind the
  entrypoint, not just `flask run`): health `200`, security headers present, and
  the full 13-step user journey passes against it.
- **Rendered-pixel sweep**: 21/21 page/width combinations (7 pages ×
  390/768/1440px) pass overflow, contrast, tap-target, heading-order, alt-text and
  control-name checks.

## 9c. Final gate (2026-11-07)

| Check | Command | Result |
|---|---|---|
| Unit + web + integration + deployment | `python3 -m unittest discover -s tests` (network) | **219 passed, 0 skipped** |
| Offline suite | `TAPEJUDGE_SKIP_CHAIN=1 …` | 219 passed, 8 skipped |
| Lint | `python3 -m ruff check .` | clean |
| Dependency audit | `python3 -m pip_audit` | no known vulnerabilities |
| Rendered sweep | `scripts/a11y_audit.mjs` | 21/21 |
| Live X Layer reads | `XLayerClient` | reachable; block 75,322,153; 415 processors; local evaluator == on-chain `eval` for circuits 1–3 |
| Production path | entrypoint + gunicorn | health 200; journey 13/13 |

## 10. Bottom line

The MVP is a **solid, honest, reproducible product** whose central integrity
promise holds under adversarial testing, and whose write surface is now gated by
authentication and object-level authorisation. It is **safe to release publicly**
once the deployment plan (TLS, `TAPEJUDGE_SECRET`, and a shared store/limiter if
more than one instance runs) is in place. The remaining risks are operational and
product-boundary, not exploitable application defects.
