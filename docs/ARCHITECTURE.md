# Architecture

TapeJudge has four layers, each with one job. The rule that ties them together:
**the score is always computed from inputs, never stored.**

```
        browser
           │  HTTP
     ┌─────▼──────┐
     │ web/app.py │  Flask: 8 screens + JSON API. No business rules.
     └─────┬──────┘
           │  Python calls
     ┌─────▼──────────┐
     │ service.py     │  TapeJudgeService: every integrity rule.
     │ (TapeJudge)    │  frozen rubric, immutable scores, recomputed results
     └──┬──────────┬──┘
        │          │
   ┌────▼────┐  ┌──▼──────────┐
   │store.py │  │ xlayer.py   │  read-only chain client
   │ SQLite  │  │ + prepare_  │  (eth_call; never sends)
   └─────────┘  │   tapeout() │
                └──┬──────────┘
                   │ uses
        ┌──────────▼───────────────────────────┐
        │ netlist.py rubric.py engine.py       │  vendored, verified
        │ verifier.py lifecycle.py chain.py    │  Mission 3 core
        │ (pure Python, dependency-free)       │
        └──────────────────────────────────────┘
```

## Why the result is recomputed, not stored

If the app stored a "final score" and served it, a reader would have to trust
that record. Instead:

1. `rubric.py` compiles the frozen rule into a circuit.
2. `engine.py` (the core competition engine) is fed the stored, immutable
   evaluations.
3. The circuit computes the weighted totals and the ranking.
4. `verifier.py` can rebuild the same inputs and re-run the same circuit.

Because the inputs (rubric + evaluations) are immutable and the function is
deterministic, re-running gives the same answer. That is what "anyone can
reproduce the scores" means in practice.

## The integrity rules, and where they live

| Rule | Enforced in |
|---|---|
| A rubric is well-formed (integer weight ≥ 1, max ≥ 1, unique names, known tie-break) | `service.py` `_build_rubric` |
| A frozen rubric cannot be replaced | `store.py` `set_rubric` (raises `PermissionError`) **and** `service.py` (raises `ConflictError`) |
| A competition cannot be frozen twice | `service.py` `freeze` |
| An entry is only accepted after the rule is published | `service.py` `add_entry` |
| A score is an integer in range, for every criterion, by a panellist, with no conflict | `service.py` `evaluate` |
| A submitted score cannot change | `store.py` UNIQUE(entry, judge) + `service.py` pre-check |
| A result with an incomplete panel cannot be finalised | `service.py` `settle` |
| The circuit independently flags an out-of-range score | `rubric.py` `valid` output |

## Data model

```
competitions(id, name, description, organiser, opens_at, closes_at, judges_json,
             status, rubric_json, rubric_digest, netlist_hex, netlist_sha256,
             n_in, n_out, gates, tie_break_json, frozen_at, settled_at,
             chain_id, processor, circuit_id, tx_hash, published_at,
             publish_mode, fee_wei, prize_note)
entries(id, competition_id, stable_id, title, summary, link, evidence_json,
        private, content_hash, created_at)          UNIQUE(competition_id, stable_id)
evaluations(id, competition_id, entry_id, judge, scores_json, justification,
            commitment, created_at)                 UNIQUE(entry_id, judge)
conflicts(competition_id, judge, entry_id, reason, created_at)
audit_events(id, competition_id, kind, payload_json, created_at)
```

`competitions.status` is one of `draft → frozen → settled`. `publish_mode` is
one of `not_published`, `local_only`, `onchain_pending_signature`,
`onchain_verified` — the app shows exactly which one is true.

## Authentication and authorisation (Mission 6)

Identity is a first-class layer now. The mechanism lives in `tapejudge/auth.py`
and the *policy* lives in `service.py`, enforced at the route boundary in
`web/app.py`.

```
        browser (session cookie)
           │
     ┌─────▼──────┐
     │ auth.py    │  mechanism: password hashing (scrypt), session + epoch,
     │            │  CSRF token, login throttle. Never reads identity from a body.
     └─────┬──────┘
           │ g.account  (resolved once per request)
     ┌─────▼──────────┐
     │ service.py     │  policy: require_organiser, judge_label, viewer-aware
     │                │  entry visibility. One AuthError for "unknown" and
     └────────────────┘  "not yours".
```

Rules:

* **Identity is taken only from the session.** A role, judge id or account id in
  a request body is never read as a fact. `g.account` is resolved once in
  `before_request` via `auth.current_account(session, store)`.
* **CSRF is enforced centrally** (`_csrf_guard`): every non-safe method needs the
  session token, so a new write route cannot be added without it.
* **A session is re-validated every request** against the account's
  `session_epoch`, so a logout-everywhere or a password change invalidates it.
  Login rotates the session id and CSRF token (defeats session fixation).
* **Object-level authorisation, not just authentication.** `require_organiser`
  raises the *same* `AuthError` for an unknown competition and a foreign one, so
  a response cannot be used to probe which competitions exist. Every organiser
  HTML route lets that error reach the central handler (404, byte-identical to an
  unknown id); the `/api/*` form is a uniform 403.
* **A judge scores only under their own membership label.** `evaluate` and
  `declare_conflict` take `judge_account_id` and require the membership to match
  the label; a label put in the request is ignored.
* **Private entries are viewer-scoped.** `get_entry` / `list_entries` take
  `viewer_account_id`; the owner and the submitter see private fields, everyone
  else sees a redacted view. Public verification never leaks a private title.
* **Verification stays public.** All read routes — including `/verify`,
  `/results` and their `/api` forms — require no account. That is the product's
  promise and it is unchanged.

## Test database isolation

A test process must never be able to open the production (or any hosted)
database. This is a hard contract, enforced at the one entry point every test
builds an app through.

**The incident it closes.** A test run inherited a deployed shell's environment —
`VERCEL` plus `POSTGRES_*` — so `make_store()` selected the live PostgreSQL DSN
and hundreds of fixture rows were written into the *production* schema.

**The contract.** `create_app(testing=True)` calls `_require_test_isolation`
before it builds anything. The guard **fails closed**: if *any* hosted-database
indicator is present it raises `RuntimeError` and refuses to start, before
`make_store()` is reached.

Indicators (`_HOSTED_DB_INDICATORS` in `tapejudge/web/app.py`):

| Variable | Why it counts |
|---|---|
| `VERCEL` | The serverless marker; `_require_production_config` treats it as hosted |
| `TAPEJUDGE_DATABASE_URL` | The DSN `make_store()` selects Postgres from |
| `DATABASE_URL`, `POSTGRES_URL`, `POSTGRES_URL_NON_POOLING`, `POSTGRES_PRISMA_URL` | Hosted-DSN aliases a deploy may expose |
| `POSTGRES_HOST`, `POSTGRES_USER` | Discrete parts: a DSN could be assembled from them |

Deliberately **not** indicators: `TAPEJUDGE_DB` (a SQLite file path — legitimate
in a test) and `TAPEJUDGE_TEST_DATABASE_URL` (the isolated contract DSN, which
never flows through `create_app`).

**Why fail closed, not fall back.** Silently degrading to SQLite would mask a
misconfigured test host: the operator would believe their Postgres tests ran when
they did not. The guard refuses instead.

**Legitimate PostgreSQL integration testing.** The store contract
(`tests/test_store_contract.py`) runs both halves. The SQLite half always runs.
The Postgres half runs only when `TAPEJUDGE_TEST_DATABASE_URL` points at a
reachable, throwaway server; each test then builds its own random `c_<hex>`
schema and never touches `TAPEJUDGE_DB_SCHEMA`. It constructs the store directly
(`PostgresStore(dsn, schema=...)`) and never calls `create_app`, so the guard is
not in its path. When the variable is unset the Postgres half is **skipped with a
reason** — a skip is not a pass.

Regression coverage: `tests/test_deployment.py`
(`test_testing_app_refuses_the_production_database`,
`test_testing_app_fails_closed_on_every_hosted_db_indicator`,
`test_testing_app_guard_does_not_hide_behind_sqlite`,
`test_testing_app_starts_clean_with_no_hosted_indicators`).

## Extension points

* **A real signer.** `xlayer.py` `prepare_tapeout` already returns the exact
  unsigned transaction. A future `publish` could hand it to a wallet; the app
  would then move `publish_mode` to `onchain_verified` only after a real receipt.
* **Email delivery for invitations.** An invitation token is shown once and only
  its SHA-256 is stored; a mailer would send the link instead of the organiser
  copying it out of band.
* **A shared store.** SQLite is fine for one instance; a multi-instance
  deployment swaps `store.py` for a networked database behind the same interface.
