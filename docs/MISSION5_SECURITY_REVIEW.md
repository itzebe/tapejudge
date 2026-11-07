# TapeJudge — Independent Security Review (Mission 5)

**Mission:** Mission 5 — independent adversarial review and release gate.
**Reviewer:** an agent acting as a separate senior engineer who did not write the MVP.
**Research date:** 2026-11-07.
**Baseline:** `/workspace/tapejudge`, local commit `f3c229a` (fresh local repo, no remote, clean tree).
**Scope:** challenge the completed TapeJudge MVP, fix confirmed defects, and decide
whether it is safe to prepare for public production release.

---

## 1. Executive summary

TapeJudge is a Flask + SQLite application whose one promise is that a competition's
scoring rule is frozen and public, and its ranking is recomputable by anyone from
the stored inputs. That promise is **enforced in the right places and holds under
adversarial testing**.

The review found and fixed **15 confirmed defects**, the most serious being:

- three ways a **read-only page returned HTTP 500** on an ordinary domain state
  (no rubric yet; a malformed stored score) — the exact failure mode a proof page
  must never have;
- **score coercion** (`true` → `1`, `5.9` → `5`) that would silently record a score
  a judge never submitted;
- a **panel/rubric size mismatch** that could let an entry be "complete" with fewer
  scores than the panel;
- a **submission race** that could surface a raw database error as a 500;
- **no security headers, unhardened cookies, and a fixed default secret**;
- **no rate limiting** on the write routes; and
- a **real mobile defect**: horizontal page scroll at 390px (measured up to 97px).

After the fixes: **135 tests pass** (7 are live-chain tests, which also pass
against X Layer when enabled), **lint is clean**, the **dependency audit is clean**,
a **27-assertion adversarial HTTP probe passes with 0 HTTP 500s**, and the layout
**no longer scrolls sideways at 390/768/1440px**.

**Release verdict:** the MVP is **ready to prepare for a restricted public
release** (public verification + invite-only competition) and **not ready for an
unrestricted public release** until **authentication** is added — the single
blocking gate. That is a scope decision, not a defect.

The deliverables:

| Document | Contents |
|---|---|
| `MISSION5_SECURITY_REVIEW.md` | this document |
| `TAPEJUDGE_THREAT_MODEL.md` | assets, actors, trust boundaries, threats |
| `TAPEJUDGE_VULNERABILITY_REGISTER.md` | every finding, severity, fix, evidence |
| `TAPEJUDGE_REGRESSION_EVIDENCE.md` | exact commands and results |
| `TAPEJUDGE_RELEASE_READINESS.md` | the release gate and its reasoning |
| `TAPEJUDGE_PRODUCTION_DEPLOYMENT_PLAN.md` | how to deploy it correctly |

---

## 2. Method

1. **Discovery.** Read the repository, `README.md`, `docs/`, the Mission 3 evidence,
   and every application module (`store.py`, `service.py`, `web/app.py`,
   `web/ratelimit.py`, `xlayer.py`, `chain.py`, templates, CSS, JS).
2. **Static adversarial read.** For each module, ask what a hostile client, a
   hostile judge, a hostile organiser and a filesystem reader could do.
3. **Live probing.** A running server was attacked with a 27-assertion HTTP probe
   (status codes, headers, XSS, traversal, SQL injection, coercion, duplicates),
   plus a 16-thread concurrency probe.
4. **Rendered-pixel verification.** Headless Chromium over the DevTools Protocol
   measured horizontal overflow and tap targets at 390/768/1440px, and a DOM audit
   checked headings, labels, `alt`, `scope` and skip links.
5. **Chain re-validation.** Read-only `eth_call` against X Layer (chain 196) to
   confirm the integration still matches its evidence.
6. **Fix + regression.** Every confirmed defect was fixed and a regression test was
   added (`tests/test_hardening.py`, 26 tests). The whole suite, lint and the
   dependency audit were re-run.
7. **Independent review.** The change was re-read as a reviewer who did not write
   it; the findings below record what that pass changed and what it could not settle.

---

## 3. What was verified to hold

These were attacked and did **not** break:

- **A frozen rubric cannot be replaced** — the store refuses a non-draft update,
  and `/verify` independently re-checks the rubric digest, netlist SHA-256 and
  netlist hex against the frozen values.
- **A committed score cannot change** — the `UNIQUE(competition_id, entry_id,
  judge)` constraint is the guard; a second submission (identical or different) is
  a clean 4xx and nothing changes. (The scoring engine treats an identical
  re-submission as idempotent; the service rejects it earlier as a conflict, which
  is the stricter and safer behaviour.)
- **A result is never stored** — `results()` and `verify()` recompute from inputs
  on every read. There is no ranking column to tamper with.
- **Local vs on-chain is never blurred** — `publish_mode` is reported verbatim; the
  UI claims "on chain" only for `onchain_verified`.
- **No secret is exposed** — `commit_key` is removed from every view; the session
  secret is never serialised.
- **No injection** — every query is parameterised; four SQL-injection payloads
  stored as inert text; XSS payloads render escaped.
- **No path traversal / IDOR** — the competition id is matched against a stored
  primary key; a non-match is 404; no filesystem path is built from it.
- **Honest failure on an unreadable chain** — the read client degrades to
  "unknown"; the fee is shown only when a getter answered.

Evidence: `TAPEJUDGE_REGRESSION_EVIDENCE.md` §5, §5b, §5c, §8.

---

## 4. Confirmed defects (fixed)

Full detail, with reproduction and evidence, is in
`TAPEJUDGE_VULNERABILITY_REGISTER.md`. Summary:

| ID | Defect | Severity | Status |
|---|---|---|---|
| V-01 | `/results` and `/verify` 500 when there is no rubric | High | FIXED |
| V-02 | `/results` and `/verify` 500 on a malformed stored score | High | FIXED |
| V-03 | An out-of-range stored score is scored instead of rejected | Medium | FIXED |
| V-04 | An uncompilable rubric returns 500 | Medium | FIXED |
| V-05 | Score coercion: `true`→1, `5.9`→5 | High | FIXED |
| V-06 | Panel size and rubric panel size can disagree | High | FIXED |
| V-07 | Concurrent duplicate submission races the check-then-insert | Medium | FIXED |
| V-08 | `Store._conn` did not roll back on error | Medium | FIXED |
| V-09 | No security headers | Medium | FIXED |
| V-10 | Session cookie flags not hardened | Medium | FIXED |
| V-11 | A fixed default `SECRET_KEY` was always used | Medium | FIXED |
| V-12 | No rate limiting on write endpoints | Medium | FIXED |
| V-13 | Horizontal page scroll at 390px | Medium | FIXED |
| V-14 | Small tap targets in header/breadcrumb | Low | FIXED |
| V-15 | `_judge_matrix` could raise on an orphaned evaluation | Low | FIXED |

### Files changed

| File | Change |
|---|---|
| `tapejudge/store.py` | `DuplicateError`; single persistent connection with `RLock`; WAL + `synchronous=FULL` + busy timeout; rollback-on-error; `weakref.finalize` close |
| `tapejudge/service.py` | `_as_int` (no bool/float coercion); caps (`MAX_CRITERIA`, `MAX_JUDGES`); panel-mismatch enforcement; unique judge ids; compile-error → `ValidationError`; defensive `results()`/`verify()`; `DuplicateError` → `ConflictError` |
| `tapejudge/web/app.py` | central error handlers (400/404/405/413/409/500); security headers; hardened cookie flags; secret handling; rate-limiter wiring; `pending.html` for no-rubric; hardened `_judge_matrix` |
| `tapejudge/web/ratelimit.py` | **new** dependency-free fixed-window rate limiter |
| `tapejudge/web/templates/pending.html` | **new** friendly no-rubric page |
| `tapejudge/web/static/style.css` | mobile fixes (wrap nav, scrollable tables, stack `dl.kv`, `min-width:0`, tap targets) |
| `tapejudge/__init__.py` | export `DuplicateError` |
| `tests/test_hardening.py` | **new** 26 regression tests |

---

## 5. Independent-review findings

Reading the change as a reviewer who did not write it surfaced four additional
points, all addressed before this report:

1. **`_conn` committed a half-finished transaction on error.** Fixed (V-08).
2. **`_judge_matrix` could `KeyError` on an orphaned evaluation.** Fixed (V-15).
3. **The API conflict status was left at 400 on purpose** to preserve the existing
   contract while removing 500s. The reviewer flags this as an intentional
   trade-off, not an oversight: a future version should return 409 consistently.
   Recorded here so it is a decision, not an accident.
4. **The rate limiter's `_limit_for` matches by bucket prefix.** A bucket named
   `evaluation` would match the `eval` rule — currently harmless (buckets are the
   fixed set `eval`/`entry`/`comp`/`misc`), but noted as a sharp edge if buckets are
   ever derived from the path more loosely.

The reviewer could **not** settle two things, stated plainly:

- Whether a *judge-collusion* attack is acceptable for the product's stated
  boundary. TapeJudge says it makes inputs auditable, not fair; the reviewer agrees
  that is a defensible boundary but notes it is the product's biggest conceptual
  exposure.
- The behaviour of the rate limiter under a *distributed* client (many IPs). It is
  a blunt instrument by design; a real deployment should add a proxy-level limit.

---

## 6. Release gate

Full reasoning in `TAPEJUDGE_RELEASE_READINESS.md`. In one line:

> **Ready to prepare for a restricted public release now. Not ready for an
> unrestricted public release until authentication and object-level authorisation
> are added to the write routes.**

The blocker is **exposure, not integrity**: the product's central promise holds,
but every write route is open, and judge identity is a recorded string rather than
a verified login. That is the one decision the gate turns on.

---

## 7. Honest limitations of this review

- **No production deployment** was performed; the deployment plan is untested
  against a live host.
- **No load testing**; concurrency was exercised at 16 threads on SQLite.
- **No wallet publication** was performed; `prepare_tapeout` was not driven to a
  signed transaction (it needs a funded signer).
- **Only Chromium** was used for the visual pass; Firefox/Safari were not exercised.
- **No penetration test of a multi-instance deployment** (there is no shared store
  to test).
- The fixes were validated by unit tests, a live HTTP probe and rendered-pixel
  measurement; they were **not** validated by a third party.
