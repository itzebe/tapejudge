# TapeJudge — controlled release evidence

Release date: **2026-11-08** (UTC). This records one verified, consistent
production release across GitHub and Vercel, with the exact commands and their
results. Every number below was produced in this session; nothing is copied from
an earlier report.

## 1. Release identity

| Item | Value |
|---|---|
| Repository | `itzebe/tapejudge` (public) |
| Default branch | `master` |
| Release commit (master HEAD) | `5b3d98d` (after PR #2) |
| Deployment implementation merge | `bb8bc66` (PR #1) |
| Vercel project | `tapejudge` (`prj_c6Nvlf262Z1B0OQfe5Tiaw6mf2xT`) |
| Production branch | `master` |
| Framework preset | `flask` |
| Production deployment | `dpl_3Br2vU5keq2KkcQ9DiUBn87ykRSr` @ `5b3d98d` |
| Public URL | <https://tapejudge.vercel.app> |

## 2. Pull requests

| PR | Title | Head | Merge commit | State |
|---|---|---|---|---|
| #1 | Deploy TapeJudge to Vercel as a durable serverless app | `vercel-serverless-deployment` @ `feb8fce` | `bb8bc663c646ca3187c2a9c65e4e6d5ca97648bb` | merged |
| #2 | Correct the README tagline: frozen, and publishable on chain | `docs/readme-publication-wording` @ `d87b7f4` | `5b3d98db212b1a73f43b2adc0d98604c844b9d0e` | merged |

PR #1 was opened as a draft and marked ready for review immediately before merge
(the REST `ready_for_review` endpoint answered 404 for this token, so the GraphQL
`markPullRequestReadyForReview` mutation was used). Both PRs merged with a merge
commit so the implementation history is preserved.

## 3. Merge gates (run on the release tree)

| Gate | Command | Result |
|---|---|---|
| Lint | `python -m ruff check .` | `All checks passed!` |
| Unit/integration (SQLite) | `python -m unittest discover -s tests` | `Ran 253 tests … OK (skipped=8)` |
| Unit/integration (Postgres) | same, with `TAPEJUDGE_DATABASE_URL=<unpooled Neon DSN>` | `Ran 253 tests … OK (skipped=8)` |
| Deployment regression | `python -m unittest tests.test_deployment` | `Ran 18 tests … OK` |
| Dependency audit | `python -m pip_audit` | `No known vulnerabilities found` (the local package is not on PyPI, so it is skipped) |
| Offline demo | `TAPEJUDGE_SKIP_CHAIN=1 python demo/run_demo.py` | completes; prints `published on chain : False (local_only)` and `No transaction was sent.` |

The 8 skipped tests are the live X Layer reads, gated on `TAPEJUDGE_SKIP_CHAIN`.

### A note on the Postgres run

Running the suite against Postgres with a forced `TAPEJUDGE_DB_SCHEMA` produces
spurious failures: that variable pins every store instance to one schema, and the
suite relies on a fresh schema per store. Without it the suite passes 253/253.
The failure was a harness mistake, not a product defect, and it is recorded here
so it is not repeated.

Each test store allocates a random `t_<hex>` schema. The suite left 78 such
schemas on the Neon database; 77 throwaway schemas were dropped and the one
production schema (`t_d69196def4cf`) was kept. Cross-checked: that schema holds
exactly the three competitions the live `/api/competitions` serves.

## 4. Vercel production

- The project is linked to GitHub (`link.type = github`, `repo = tapejudge`,
  `org = itzebe`, `productionBranch = master`). GitHub auto-deploy is enabled
  (`createDeployments: enabled`); the merge of PR #1 produced a production
  deployment at the merge commit with `source: git`.
- `tapejudge.vercel.app` aliases the newest production deployment. After the
  merge of PR #2 the alias moved to `dpl_3Br2vU5keq2KkcQ9DiUBn87ykRSr` @ `5b3d98d`.
- The deployment build log shows `Using Python 3.12 from pyproject.toml`, `uv`
  installs the dependencies, and the build completes in seconds.
- Runtime error logs for the release deployment over the preceding two hours:
  **0 entries**.
- The Vercel-assigned `*.vercel.app` deployment URLs are behind Vercel
  authentication (`all_except_custom_domains`); the custom domain is public, which
  is what the smoke tests below exercise.

## 5. Secret rotation

`TAPEJUDGE_SECRET` signs the Flask session cookie (which also carries the CSRF
token) and is shared by the production and preview environments. It was rotated
in place with a freshly generated 256-bit value (`secrets.token_hex(32)`); the
value was passed to the Vercel API inside a script, never printed, and never
placed in a command line or a log. The rotation invalidates all previously signed
cookies — users simply sign in again with their password; there is no lockout,
because the account and its password hash are unaffected.

After the rotation the project was redeployed so the new value took effect, and a
fresh sign-in was verified against production:

```
POST /login  -> 302 /   (organiser@demo.tapejudge.test)
GET  /api/me -> {"authenticated": true}
```

No build log contained the variable's value (scanned for the variable name and
for any 64-hex string: 0 hits).

## 6. Live production smoke test

| Path | Status |
|---|---|
| `/` | 200 |
| `/login`, `/signup`, `/new` | 200 |
| `/api/health` | 200 (`{"chain_reachable":true,"ok":true}`) |
| `/api/competitions` | 200 |
| `/favicon.ico`, `/static/style.css` | 200 |
| `/api/nope` | 404 |
| `/robots.txt`, `/sitemap.xml` | 404 (not served — see limitations) |

Security headers on `/`:

```
content-security-policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';
  script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
referrer-policy: same-origin
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
```

### Authenticated end-to-end journey (HTTP, production)

`node scripts/journey_probe.mjs https://tapejudge.vercel.app` — **13/13 checks
pass**, including: anonymous create refused (401), anonymous `/new` redirects to
login, organiser sign-in, competition created, write without CSRF refused (403),
rubric frozen, invitation issued, judge scored, public verify reachable
anonymously, public results reachable anonymously, ranking shown, verify
reproduces the ranking, and the frozen-rule checks pass
(`netlist_hex_matches`, `netlist_sha256_matches`, `rubric_digest_matches`).

### Accessibility

`node scripts/a11y_audit.mjs https://tapejudge.vercel.app / /login /signup /new`
at 390/768/1440 px — **12 page/viewport combinations, 0 failing**: no horizontal
overflow, one `h1` per page, no skipped heading levels, no undersized non-prose
targets, no low-contrast text, no images without `alt`, no unnamed controls.

## 7. Honesty of the on-chain claim

The application never sends a transaction and never reports a local circuit as
on-chain. On production the verify page states, for an unpublished competition:

> **Not published on chain** … A locally compiled circuit is *not* an on-chain
> circuit. The arithmetic below is reproducible, but there is no chain read to
> compare it against yet.

The template only renders "The circuit is published on X Layer" when
`c.publication.on_chain` is true.

One documentation overclaim was found and fixed during this release: the README
front-page tagline said the rule "**is published** and frozen through TapeOut",
while nothing is published by default and the UI says the opposite. PR #2 changed
the tagline to "whose rule is **frozen as** a TapeOut circuit" and the promise
table row to "The rule is **frozen, and can be published on chain**". The code,
the UI and the submission docs (`docs/HACKATHON_SUBMISSION.md` already states
"Circuit ID: none yet") were already accurate.

## 8. Environment configuration (names only)

`TAPEJUDGE_SECRET` (production, preview), `TAPEJUDGE_DATABASE_URL` (production,
preview), `TAPEJUDGE_DB_SCHEMA` (separate production and preview values),
`TAPEJUDGE_ENABLE_HSTS` (production), plus the Neon-injected Postgres variables.
No secret value is recorded in this document or in the repository.

## 9. Remaining limitations (honest)

- `/robots.txt` and `/sitemap.xml` are not served (404). They were not in scope
  for this release; a crawler will simply find no directives. Low impact for a
  demo, worth adding before a public launch.
- The hosted checkout/competition data lives in the Neon Postgres schema
  `t_d69196def4cf`. This is durable across invocations (proven by the
  cross-invocation persistence of the three competitions), but it is a single
  shared schema: a future change to the schema naming would need a migration.
- No transaction has been published on chain, so no circuit id exists. This is a
  product decision (the organiser's wallet must sign a funded `tapeout`), not a
  defect.
- This release did not perform a physical-wallet signing test; signing paths are
  covered by the unit/integration suite only.
