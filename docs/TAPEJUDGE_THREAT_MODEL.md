# TapeJudge — Threat Model

Mission 5 (independent adversarial review) with the Mission 6 authentication
work applied. Research date **2026-11-07**. Baseline commit `f3c229a`; the
reviewed working tree adds the hardening in `TAPEJUDGE_VULNERABILITY_REGISTER.md`
and the identity/authorisation layer in `MISSION6_SECURITY_REVIEW.md`.

This model states, for each asset and each actor, what the system actually
protects and what it explicitly does not. It is written to be checkable against
the code, not aspirational.

---

## 1. What the product claims (and therefore what an attacker would target)

TapeJudge's one promise: **the scoring rule is fixed and public, and the final
ranking is recomputable from the stored inputs by anyone, for free.**

The integrity properties that follow, and that an attacker would try to break:

| Property | Where it is enforced |
|---|---|
| A published (frozen) rubric cannot be replaced | `Store.set_rubric` refuses when `status != 'draft'`; `TapeJudgeService.set_rubric` checks first |
| A submitted evaluation cannot change | `UNIQUE(competition_id, entry_id, judge)` in `evaluations`; `DuplicateError` → `ConflictError` |
| A result is never stored | `results()` / `verify()` recompile and re-run every read; there is no ranking column |
| Local ≠ on-chain is never blurred | `publish_mode`; `publication_view()`; the UI prints the mode verbatim |
| A number shown was computed or read | every price/fee/digest is computed at read time; an unreadable chain read degrades to "unknown" |

## 2. Assets

| Asset | Sensitivity | Where it lives |
|---|---|---|
| Frozen rubric + netlist + digests | **Public** (that is the point) | `competitions` table, `/verify`, `/api/.../verify` |
| Committed evaluations (scores) | **Public after commit** | `evaluations` table |
| Account email + display name | **Personal data** | `accounts` table; never returned by any public view |
| Account password | **Secret (hashed only)** | `accounts.password_hash` — scrypt via werkzeug; the plaintext is never stored |
| Judge membership (account ↔ label) | Authorisation-critical | `memberships` table |
| Invitation token | **Secret, single-use** | only its SHA-256 is stored (`invitations.token_hash`) |
| `commit_key` (HMAC key for commitments) | **Secret** | `competitions.commit_key`; never returned by any view |
| `SECRET_KEY` (Flask session/flash signing) | **Secret** | process config, from `TAPEJUDGE_SECRET` or a per-process random |
| SQLite file | Integrity-critical | on disk at `TAPEJUDGE_DB` |
| X Layer RPC access | Read-only, unauthenticated | `https://rpc.xlayer.tech` |

There is **no** API key, **no** wallet key and **no** private key in the system.
Account passwords are the only credential, and they are stored as a scrypt hash.
The threat surface that grew with Mission 6 is the account store (email +
password hash) and the session cookie; both are addressed in §5.

## 3. Actors

| Actor | Capability assumed |
|---|---|
| Anonymous internet client | Can send any HTTP request to any route |
| Registered account (stranger) | Holds a valid session; not entitled to any specific competition |
| Malicious entrant | Can submit entries, attempt to spam |
| Malicious judge | Holds a judge membership; can submit scores and declare conflicts |
| Malicious organiser | Creates the competition, sets the rubric, freezes, settles |
| Network attacker | Can observe/modify traffic if TLS is absent |
| Compromised host / filesystem reader | Can read the SQLite file directly (password hashes, token hashes) |

## 4. Trust boundaries

1. **HTTP request → Flask app.** All input crosses here. JSON bodies and form
   fields are parsed and validated before any store write.
2. **Flask app → SQLite.** All queries are parameterised (`?` placeholders). No
   string-built SQL exists in the codebase.
3. **Flask app → X Layer RPC.** `eth_call` only. No key, no transaction, no
   signature. The RPC is treated as untrusted: a bad/short/empty response is
   never coerced into a number (see the fee getter and `isRpcUnreachable`
   handling in the read client).
4. **Application → wallet (publication).** The app builds an **unsigned** call
   and stops. The authorisation boundary is the human's wallet.

## 5. Threats, and the system's honest response

| # | Threat | Response | Residual risk |
|---|---|---|---|
| T1 | Replace a frozen rubric to change the outcome | Refused at the store; verified by the `/verify` digest re-check | None observed. An organiser can create a *new* competition, which is not a violation of the promise |
| T2 | Change a score after commit | `UNIQUE` constraint + commitment; a second different submission is a 4xx | None observed |
| T3 | Two concurrent identical submissions race the check-then-insert | The DB `UNIQUE` constraint is the real guard; `DuplicateError` → clean 4xx | None; the loser gets a conflict, one row is stored (proven by a 16-thread test) |
| T4 | Fabricate a ranking by editing a stored result | There is no stored result | None |
| T5 | Claim a local circuit is on-chain | `publish_mode` is reported verbatim; `on_chain` is true only for `onchain_verified` | None observed |
| T6 | Break a read-only page with a malformed stored row | `results()` / `verify()` catch and report malformed rows; the page renders | None observed; the malformed row is surfaced, not hidden |
| T7 | Crash a page with an out-of-range or non-JSON stored score | Validated on read; reported as `malformed` | None observed |
| T8 | Coerce a score (`true`→1, `5.9`→5) | `_as_int` rejects bools and floats at the API and the domain | None observed |
| T9 | Path traversal / IDOR via the competition id | The id is a URL segment matched against a stored primary key; a non-match is 404; no filesystem path is built from it | None observed |
| T10 | SQL injection | Parameterised queries only | None observed |
| T11 | XSS via a competition/entry name | Jinja2 autoescaping; a `<script>` name renders escaped | None observed |
| T12 | Secret leakage in an API view | `commit_key` popped from every view; `SECRET_KEY` never serialised | None observed |
| T13 | Forge a session cookie | Production refuses a fixed default secret; a random per-process secret is used when none is configured | Sessions do not survive a restart without `TAPEJUDGE_SECRET` (accepted) |
| T14 | Request-body flooding | `MAX_CONTENT_LENGTH` = 256 KiB → 413 | Accepted: a 256 KiB body is still processed; the limiter covers volume |
| T15 | Write-endpoint spam (entry/evaluation flooding) | In-process fixed-window rate limiter, per (ip, bucket) | Accepted: in-process only, so a multi-instance deploy would need a shared limiter (see the deployment plan) |
| T16 | Clickjacking | `X-Frame-Options: DENY`, `frame-ancestors 'none'` | None |
| T17 | MIME sniffing / referrer leakage | `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` | None |
| T18 | A judge is not who they claim — anyone could submit a judge's scores | **Defended (Mission 6).** A judge scores only under the label bound to their authenticated account; the label in the request is ignored; the judge page and evaluation API require a session holding that membership | The account is only as strong as its password; a shared password is a shared identity (accepted — a human factor) |
| T19 | Judges collude | **Not defended.** TapeJudge makes inputs auditable, not fair | Documented in the UI ("What this proves, and what it does not") |
| T20 | A stalled X Layer RPC hangs a request | Read client uses a bounded `urlopen` timeout | Accepted: a slow-but-not-dead RPC costs the timeout on the read path only |
| T21 | TLS downgrade | Left to the deployment (reverse proxy / platform) | Accepted; the app does not terminate TLS itself |
| T22 | CSRF: a third-party page drives a write with the victim's cookie | `SameSite=Lax` cookie **and** a per-session CSRF token required on every non-safe method, centrally enforced | None observed |
| T23 | Session fixation: an attacker sets a known session id | Login rotates the session id and the CSRF token (`auth.rotate_session`) | None observed |
| T24 | Stolen session stays valid after logout / password change | Every request re-validates against the account `session_epoch`; logout-everywhere and password change bump it | None observed |
| T25 | Credential stuffing / brute force on login | `LoginThrottle` (per-email, fixed window) plus the global write limiter; login never reveals whether an email exists | Accepted: in-process limiter; a multi-instance deploy needs a shared one |
| T26 | Password theft from the database | scrypt hashes only (werkzeug `generate_password_hash`), constant-time verification; plaintext is never stored or logged | Accepted: a filesystem reader can still attempt an offline crack of a weak password |
| T27 | Cross-competition / IDOR: act on a competition you do not own | `require_organiser` raises one uniform `AuthError`; HTML → 404 byte-identical to an unknown id, API → uniform 403 | None observed |
| T28 | Existence oracle: tell "not mine" from "does not exist" | Every organiser HTML route lets the `AuthError` reach the central handler (fixed in Mission 6 — `rubric`, `publish`, `results` previously rendered a 200) | None observed |
| T29 | A private entry's title leaks through public verification | `verify()` redacts private fields for a non-owner viewer; asserted by test | None observed |
| T30 | An unauthenticated client creates a competition / submits an entry | Every write route requires a session **and** a CSRF token; anonymous writes are refused | None observed |

## 6. Explicit non-goals

TapeJudge does **not** attempt to prove that judges scored fairly, that a judge
is who they claim to be, that criteria were the right ones, or that entrants are
honest. It fixes the rule and makes the arithmetic reproducible. This boundary
is stated in the product UI, not only in this document.

## 7. The most important remaining risk

**Judicial fairness is still out of scope (T19), and identity is only as strong
as a password (T25/T26).** TapeJudge now proves *which account* submitted a
score, not that the judge was fair. That boundary is deliberate and stated in the
product UI.

The Mission 5 blocker — **no authentication (formerly T18/A-01)** — is closed:
every write route requires an authenticated session and a CSRF token, judges are
bound to their membership, and organiser actions are object-scoped. The
unresolved operational residuals are the in-process rate limiter and the
single-file SQLite store, both of which are deployment-shape decisions
(`TAPEJUDGE_PRODUCTION_DEPLOYMENT_PLAN.md`), not application defects.
