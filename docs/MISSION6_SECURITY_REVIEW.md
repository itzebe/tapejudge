# TapeJudge — Authentication & Authorisation Review (Mission 6)

**Mission:** Mission 6 — implement secure authentication and object-level
authorisation, closing Mission 5's single release blocker (unauthenticated write
access), while preserving public access to independent verification.
**Reviewer:** an agent acting as a separate senior engineer.
**Research date:** 2026-11-07.
**Baseline:** `/workspace/tapejudge`, local commit `b173b35` (fresh local repo,
no remote).
**Scope:** implement identity, then adversarially test every write route for
authentication, CSRF, object-level authorisation and information leakage.

---

## 1. Executive summary

Mission 5 recorded one blocking gap: **no authentication** — every write route
was open to anyone who could reach the server (its residual `A-01`). This mission
closes it.

The design keeps mechanism and policy apart:

- **Mechanism** — `tapejudge/auth.py`: scrypt password hashing, a rotating
  session with a `session_epoch`, a per-session CSRF token, and a per-(ip, email)
  login throttle. Identity is read **only** from the session; a role, judge id or
  account id in a request body is never treated as a fact.
- **Policy** — `tapejudge/service.py`: `require_organiser`, judge-membership
  binding, and viewer-scoped entry visibility. It raises **one** `AuthError` for
  "unknown competition" and "not yours", so a refusal cannot be used to probe
  existence or ownership.
- **Enforcement** — `tapejudge/web/app.py`: a central CSRF guard on every
  non-safe method and a central `AuthError` handler (HTML → 404 identical to an
  unknown id; API → uniform 403).

**The review found and fixed one new defect (V-16):** the organiser HTML routes
`rubric`, `publish` and `results` swallowed the ownership gate's `AuthError` into
a flash and returned **200**, while an unknown competition returned **404** — an
existence oracle that broke the product's own indistinguishability invariant.
All three now let the `AuthError` reach the central handler and return a
byte-identical 404.

After the fix: **196 tests pass** (7 live-chain tests skip offline and pass
against X Layer when enabled), **lint is clean**, the **dependency audit is
clean**, and an adversarial HTTP probe confirms:

- every anonymous write is refused (**403**) and every public read is served
  (**200**);
- a logged-in non-owner receives a **byte-identical 404** for a foreign and an
  unknown competition on all four organiser routes;
- a stranger cannot score (`'None' is not on the panel`), cannot spoof a judge
  label, and cannot read a private entry title;
- a write with a valid session but **no CSRF token** is refused (**403**).

**Verdict:** the Mission 5 blocker is closed. The application is **ready for a
public production release** of the full surface (public verification, organiser
competitions, judge scoring, entry submission), conditional on the deployment
plan (TLS, `TAPEJUDGE_SECRET`, and a shared store/limiter if more than one
instance runs).

---

## 2. What was implemented

| Concern | Where | Rule |
|---|---|---|
| Password storage | `auth.hash_password` / `verify_password` | scrypt (werkzeug default); plaintext never stored, logged or compared with `==`; verification is constant-time |
| Registration / login | `web/app.py` `signup`, `login`; `service.register_account`, `authenticate` | email normalised + validated; duplicate email refused; wrong password and unknown email return the **same** 401 |
| Session | `auth.rotate_session`, `current_account` | the session stores `account_id` + `session_epoch`; login clears pre-login state, then rotates the session id **and** the CSRF token (defeats fixation); every request re-reads the account and rejects a stale epoch |
| Logout everywhere / password change | `store.bump_session_epoch`; `service.change_password`, `logout_everywhere` | bumping the epoch invalidates every other session |
| CSRF | `auth.csrf_ok`; `web/app.py` `_csrf_guard` | every non-safe method needs the session token (form field or `X-CSRF-Token`); enforced centrally so a new route cannot omit it |
| Login throttle | `auth.LoginThrottle`; `login` route | fixed window per (ip, email); a throttled request and a wrong password are indistinguishable in message |
| Judge membership | `service.invite_judge`, `accept_invitation`, `judge_label`; `store.memberships` | a single-use invitation (only its SHA-256 is stored) binds an **account** to a judge **label** |
| Object-level authorisation | `service.require_organiser`; `web/_require_organiser` | one uniform `AuthError` for unknown/foreign; used by every organiser action |
| Judge scoring binding | `service.evaluate`, `declare_conflict` | the label is the caller's own membership label; a label in the body is ignored |
| Entry visibility | `service.get_entry`, `list_entries` (`viewer_account_id`) | owner and submitter see private fields; everyone else sees a redacted view, including through public verification |

### 2.1 The uniform-refusal invariant (and the defect it caught)

`service.require_organiser` raises the *same* message and type whether the
competition is unknown or owned by someone else. For that to be true on the
wire, the route must not catch the error itself. The `judges` route already
called `_require_organiser` **outside** its `try`, so its `AuthError` reached the
central handler. `rubric`, `publish` and `results` called it **inside** the
`try`, where `except (ValidationError, ConflictError, AuthError)` turned it into
a flash and a normal 200 page. That is **V-16**; the fix moves the gate outside
the `try` on all three.

---

## 3. Adversarial verification

All probes run against the live app on `http://127.0.0.1:12000` (Flask dev
server, file-backed SQLite). "Anon" = no cookie jar.

### 3.1 Anonymous write probe — every write refused

| Route | Method | Anon status |
|---|---|---|
| `/new` | POST | 403 |
| `/c/<id>/rubric` | POST | 403 |
| `/c/<id>/publish` | POST | 403 |
| `/c/<id>/judges` | POST | 403 |
| `/c/<id>/submit` | POST | 403 |
| `/c/<id>/judge` | POST | 403 |
| `/c/<id>/results` | POST | 403 |
| `/api/competitions` | POST | 403 |
| `/api/competitions/<id>/rubric` | POST | 403 |
| `/api/competitions/<id>/freeze` | POST | 403 |
| `/api/competitions/<id>/entries` | POST | 403 |
| `/api/competitions/<id>/evaluations` | POST | 403 |

The 403 is the CSRF guard firing first (no session → no token). This is the
correct, uniform refusal: an anonymous caller cannot distinguish "you need to
sign in" from "you need a token".

### 3.2 Public reads remain anonymous — the product's promise

| Route | Method | Anon status |
|---|---|---|
| `/` | GET | 200 |
| `/c/<id>/verify` | GET | 200 |
| `/c/<id>/results` | GET | 200 |
| `/api/health` | GET | 200 |
| `/api/competitions` | GET | 200 |
| `/api/competitions/<id>/verify` | GET | 200 |

The verification page still renders with no account: *"No account, no wallet, no
trust in this page."*

### 3.3 Object-level authorisation — a stranger cannot tell "not mine" from "unknown"

Logged in as a fresh "Stranger" account (valid session, valid CSRF token),
POSTing to each organiser route:

| Route | Foreign competition | Unknown competition |
|---|---|---|
| `/c/<id>/rubric` | 404 | 404 |
| `/c/<id>/publish` | 404 | 404 |
| `/c/<id>/results` | 404 | 404 |
| `/c/<id>/judges` | 404 | 404 |

The bodies are **byte-for-byte identical** (asserted by
`tests/test_auth.py::TestCrossCompetitionAuthorisation::test_non_owner_html_write_is_indistinguishable_from_unknown`).
The API form (`/api/competitions/<id>/rubric`) is a uniform **403** with an
identical JSON body for both cases.

### 3.4 Judge binding — a stranger cannot score

| Action | Result |
|---|---|
| `GET /c/<id>/judge` as a non-judge | 200, page states you are not a judge (no scores offered) |
| `POST /api/competitions/<id>/evaluations` as a non-judge | 400 `'None' is not on the panel for this competition` |
| `POST /c/<id>/judge` (form) as a non-judge | 200, flash `you are not a judge on this competition` |
| Judge scoring under their own label | 201, `{"judge":"judge.alice","scores":{"demo":9}, …}` |
| Judge scoring a second time | 400 `'judge.alice' has already scored 'alpha'; a submitted score cannot change` |

### 3.5 End-to-end judge flow (the Mission 6 acceptance path)

Driven entirely over HTTP (`/tmp/qa_flow.py`):

1. Organiser signs up → 200.
2. Creates competition "QA Attr …" with judge `judge.alice` → redirect to
   `/c/comp_e0302b8c5c74/rubric`.
3. Configures the rubric (`3×demo`, max 10) → 200.
4. Freezes it → 200.
5. Submits entry `alpha` → 200.
6. Invites `judge.alice` → 200, single-use token shown once.
7. Alice signs in and accepts the invitation → redirect to
   `/c/comp_e0302b8c5c74/judge` ("Invitation accepted. You are a judge on this
   competition.").
8. Alice submits `{"entry":"alpha","scores":{"demo":9}}` → **201**, recorded as
   `judge.alice`.
9. Anonymous reader fetches `/c/comp_e0302b8c5c74/verify` → **200**, ranking
   `alpha total 27` (`3 × 9`), with the frozen-rule digest re-checked.
10. Anonymous `/api/competitions/comp_e0302b8c5c74/results` reports
    `identity.authenticated: 1`, `unauthenticated: 0` — the score is attributed
    to a **verified account**, not a bare identifier.

### 3.6 CSRF and private-entry confidentiality

| Probe | Result |
|---|---|
| Authenticated session, write with **no** CSRF token | **403** |
| Private entry title in anonymous `/verify` | **not present** |
| Private entry title in anonymous `/api/.../results` | **not present** |
| Private entry title for the **owner** | present (correct) |
| Private entry title for a **different signed-in account** | **not present** |

---

## 4. Regression coverage

`tests/test_auth.py` (59 tests) plays an attacker who holds a valid session but
is not entitled to the object, and asserts refusal **without** leaking existence.
It exercises the real Flask app and the real service, so it fails if a guard is
removed from either layer. Groups:

- identity is taken only from the session (a body cannot supply an owner, a role,
  or a judge label);
- object-level authorisation (rubric, freeze, settle, judges) including the
  byte-identical refusal invariant (V-16);
- the service-level gate directly (no HTTP);
- private-entry visibility (owner, submitter, stranger, anonymous);
- invitation lifecycle (single-use, wrong email, unknown/expired/revoked token,
  panel membership);
- session mechanics (rotation, epoch invalidation, logout-everywhere, no
  account-existence disclosure, throttle);
- CSRF (missing, wrong, correct, and GET is exempt);
- legacy evaluations recorded before authentication are reported as
  `unauthenticated` and reproduced unchanged.

---

## 5. Residual risks (honest)

| ID | Residual | Why accepted |
|---|---|---|
| A-02 | Judge fairness is not proven — TapeJudge proves *which account* acted, not whether the judge was fair. | Product boundary, stated in the UI. |
| A-03 | The rate limiter and login throttle are **in-process**. | Correct for a single instance; a multi-instance deploy needs a shared limiter. |
| A-05 | SQLite is single-writer, one file. | Fine for one instance; a shared store is a deployment decision. |
| A-07 | Identity strength is the account password. | scrypt + throttling; a weak or shared password is a human factor. |
| — | No email delivery: an invitation link is shown once and shared out of band. | Deliberate MVP scope; the token is single-use and hashed at rest. |
| — | No TLS termination in the app. | Required in front of it (deployment plan). |

---

## 6. What was **not** verified

- **No production deployment** and **no TLS** — the mission is implementation +
  verification on a local dev server.
- **No load test.** Concurrency is exercised in the suite (16 threads on SQLite),
  not at production scale.
- **No real wallet publication.** `prepare_tapeout` builds the unsigned call and
  stops; the `tapeout` transaction was not sent (it needs a funded signer).
- **No browser matrix beyond the available browser.** The visual pass used the
  workspace browser; Firefox/Safari were not exercised, and headless Chromium
  was unavailable for pixel measurement in this session.

---

## 7. Method

- **Static read** of `auth.py`, `service.py`, `store.py`, `web/app.py` and the
  templates, tracing every write route to its authentication, CSRF and
  object-level checks.
- **Live HTTP probe** with per-session cookie jars: the anonymous write sweep,
  the public-read sweep, the stranger object-authorisation sweep, the
  judge-binding probes, the full invitation→score→verify flow, and the CSRF and
  private-entry probes.
- **Unit + integration suite** (`python3 -m unittest discover -s tests`, 196
  tests) and `tests/test_auth.py` in isolation.
- **Lint** (`python3 -m ruff check .`) and **dependency audit** (`pip-audit`).
