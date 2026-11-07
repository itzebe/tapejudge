# TapeJudge — Release Readiness Assessment (Mission 5)

Research date **2026-11-07**. Baseline `f3c229a`; reviewed working tree includes
the hardening in `TAPEJUDGE_VULNERABILITY_REGISTER.md`.

This is the release gate. It states one verdict and the reasoning behind it,
against the product's real promise and the realities of a public deployment.

---

## Verdict

> **NOT READY for an unrestricted public production release.**
> **READY to prepare for a restricted release** (public read/verification, and a
> closed/organiser-operated competition), **conditional on the deployment plan
> in `TAPEJUDGE_PRODUCTION_DEPLOYMENT_PLAN.md` and on one blocking gate: adding
> authentication before any write route is exposed to the public.**

The application is **functionally complete, internally consistent, and hardened**
against the classes of defect this mission set out to find. It is **not** safe
to expose its write routes to the open internet, because it has no
authentication — and that is a design gap, not a bug this mission could fix
without changing the product's scope.

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
| **Exposure / access control** | Who can reach the write routes? | **Anyone.** This is the blocker (§4) |

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

## 4. Exposure — the blocking gate

**There is no authentication.** Every route is open to anyone who can reach the
server:

- `POST /new`, `/c/<id>/submit`, `/c/<id>/judge`, `/c/<id>/rubric`,
  `/c/<id>/publish`, `/c/<id>/results` and the `/api/*` writes all accept
  anonymous input.

For a competition *this is the whole point of the surface* — entrants and judges
must be able to act. But for an **unrestricted public deployment** it means:

1. Anyone can create a competition and consume resources.
2. Anyone can submit entries to any open competition.
3. **Anyone who knows a judge's identifier can submit that judge's scores** —
   judge identity is a recorded string, not a verified login.
4. The rate limiter blunts volume but does not gate identity.

The mission deliberately did not add an identity system; that is a product
decision with real scope, not a defect to patch silently. **This is the gate.**

## 5. Deployment reality

| Item | State | Note |
|---|---|---|
| Persistence | SQLite, one file | Fine for one instance; not multi-instance |
| Rate limiting | in-process | Correct for one instance; a shared limiter is needed to scale out |
| Secret management | `TAPEJUDGE_SECRET` env var | Must be set in production (the app refuses a fixed default) |
| TLS | not terminated by the app | Required in front of it |
| Chain access | read-only `eth_call` | No key, no spend |
| Publication | prepared, not sent | Needs a funded signer — a separate, deliberate step |

## 6. Readiness by release shape

| Release shape | Verdict | Conditions |
|---|---|---|
| **Public read-only / verification** (anyone may view a frozen competition and reproduce it) | **Ready** | Set `TAPEJUDGE_SECRET`; TLS; single instance |
| **Organiser-operated, invite-only competition** (writes gated by the organiser out-of-band) | **Ready with care** | The organiser must treat judge identifiers as shared secrets and watch the access log; rate limiting on |
| **Unrestricted public competition** (open sign-up, self-service writes) | **NOT ready** | Requires authentication + authorisation on every write route |

## 7. Blocking items before an unrestricted public release

1. **Authentication** for organisers, entrants and judges (the top gate).
2. **Authorisation**: a judge may only score the competition they are on; an
   organiser may only manage their own competition (object-level checks).
3. **A shared rate limiter** if more than one instance runs.
4. **A shared store** (Postgres) if more than one instance runs.
5. **Operational**: log retention, backups of the SQLite file, and an alert on
   5xx.

None of these is a correctness defect; they are the difference between "a
working MVP" and "a public service". They are scoped in the deployment plan.

## 8. Non-blocking, recommended

- A `<meta name="robots">` policy and a `robots.txt` if the deployment should not
  be indexed.
- Structured request logging (the app currently uses Flask's default logger).
- A `Content-Security-Policy-Report-Only` pass before enforcing the strict CSP.

## 9. What this mission changed to reach this verdict

See `TAPEJUDGE_VULNERABILITY_REGISTER.md`: 15 confirmed defects fixed (500s on
ordinary states, score coercion, panel mismatch, a submission race, missing
security headers/cookies/secret handling, no rate limiting, and a real mobile
overflow), each with a regression test. 135 tests pass; lint and dependency
audit are clean.

## 10. Bottom line

The MVP is a **solid, honest, reproducible product** whose central integrity
promise holds under adversarial testing. It is **safe to prepare for a
restricted public release now**, and **safe for an unrestricted public release
only after authentication and object-level authorisation are added** — the
single decision this gate turns on.
