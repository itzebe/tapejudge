# TapeJudge

**Competition scoring whose rule is published and frozen through TapeOut, and
whose final scores anyone can independently reproduce.**

TapeJudge is a working web application for running a judged competition on the
X Layer ecosystem. An organiser writes a scoring rule; TapeJudge compiles that
rule into a TapeOut circuit; the rule is frozen and can be published to X Layer.
Judges commit integer scores. The ranking is computed by the frozen circuit —
never stored — so anyone can re-run it for free and get the same numbers.

It is **not** a leaderboard for a single competition and it is **not** a general
"AI judge". It is the piece that is usually missing from a judged competition:
a fixed, public rule and a reproducible result.

---

## The promise, precisely

| The promise | How TapeJudge keeps it |
|---|---|
| **The rule is published and frozen.** | The rubric is compiled into a circuit and hashed. Freezing is irreversible: the store refuses to replace a frozen rubric, and the service refuses to freeze twice. |
| **Evaluations follow those rules.** | Every score is validated against the frozen rubric (integer, in range, every criterion present) before it is stored. The circuit's own `valid` output is a second, independent check. |
| **Anyone can reproduce the final scores.** | The result is recomputed from stored inputs every time it is requested — there is no stored "final score" to trust. `/verify` recompiles the rule, re-packs the scores and re-runs the same TAP-02 evaluator, and shows the exact call to reproduce it on chain. |

### What it does *not* promise (and says so in the UI)

TapeJudge does **not** prove that judges scored fairly, that a judge is who they
claim to be, or that the criteria were the right ones. It makes the rule fixed
and the arithmetic reproducible. Conflicts of interest, bias and collusion are
human problems; TapeJudge makes the inputs auditable so a dispute is about facts
rather than arithmetic.

---

## Quick start

Requires Python 3.11+ (the container used for development runs 3.13). The only
third-party runtime dependency is Flask.

```bash
pip install -e .            # or: pip install flask

# Run the app (SQLite file, so data survives a restart)
TAPEJUDGE_DB=./tapejudge.db TAPEJUDGE_PORT=8080 python -m tapejudge.web.app

# Or, with the console script
tapejudge
```

Open <http://127.0.0.1:8080>.

To see the whole workflow without clicking through the UI, run the demo:

```bash
python demo/run_demo.py            # local only
python demo/run_demo.py --chain    # also do a read-only X Layer cross-check
```

To seed a **synthetic** demonstration competition into a database file:

```bash
python -m tapejudge.seed ./tapejudge.db
```

Everything the seeder writes is labelled `SYNTHETIC` and is invented for
demonstration — no real users, judges, entries or scores.

---

## The eight screens

| Route | Purpose |
|---|---|
| `GET /` | Competition list. Every row shows the frozen-rule digest and the circuit size. |
| `GET/POST /new` | Create a competition: name, organiser, dates, judging panel. |
| `GET/POST /c/<id>/rubric` | Configure criteria (weight, max score), panel size, tie-break. Shows the rule in plain language, the circuit size, and the compiled netlist. |
| `GET/POST /c/<id>/publish` | Freeze the rule and prepare publication. Shows the honest publication state and the unsigned transaction boundary. |
| `GET/POST /c/<id>/submit` | Entrants submit entries. Private entries publish only a content hash. |
| `GET/POST /c/<id>/judge` | A judge scores entries. Submitted scores are final and read-only. |
| `GET/POST /c/<id>/results` | The ranking, per-criterion subtotals, incomplete-panel warnings, and finalisation. |
| `GET /c/<id>/verify` | Reproduce the result yourself: recompute the rule, re-run the evaluator, show the chain call. |

A JSON API mirrors the workflow (`/api/competitions`, `/api/competitions/<id>/…`)
for scripted use.

---

## X Layer integration

TapeJudge talks to X Layer **read-only** unless a human explicitly signs a
transaction. It never holds a key and never sends a transaction on your behalf.

* **Reads** (`tapejudge/xlayer.py`): `cpuCount()`, `cpuAt()`, `nextId()`,
  `circuitInfo()`, `netlist()`, and `eval(circuitId, inputs)` — all `eth_call`.
* **Publication** is *prepared*, not sent: the app builds the exact unsigned
  `tapeout(bytes,uint32,uint32)` transaction, reads the real fee where the
  processor exposes one, and stops at the authorisation boundary. The
  organiser's wallet must sign it.
* The local evaluator is checked against an existing on-chain circuit: for the
  same input bytes, the local TAP-02 evaluator and the on-chain `eval` agree.

`TAPEOUT_IDEA_SCOUTS_*`-style marketing language is deliberately absent here.
This file describes what the code does.

---

## Honesty model

TapeJudge has one rule: **never show a number it did not compute or read, and
never claim a state it is not in.**

* An unreadable chain read degrades to an honest "unknown", never to `0`.
* A locally compiled circuit is reported as `local_only`, never as "on chain".
* The fee is shown only when a getter actually answered; otherwise it is
  "unknown".
* An out-of-range score is rejected at the API and, independently, makes the
  circuit report `valid = 0`.
* A result with an incomplete panel is shown as incomplete and cannot be
  finalised.

---

## Layout

```
tapejudge/
  netlist.py  rubric.py  engine.py  verifier.py  lifecycle.py  chain.py
        # Vendored, verified Mission 3 core. Pure Python, dependency-free.
        # Byte-faithful copies; do not reformat.
  store.py        # SQLite persistence. One file; no server.
  service.py      # TapeJudgeService: every integrity rule lives here.
  xlayer.py       # Read-only X Layer client + prepare_tapeout().
  seed.py         # SYNTHETIC demonstration data.
  web/
    app.py        # Flask app, routes, JSON API.
    templates/    # The eight screens.
    static/       # CSS, a little progressive-enhancement JS, favicon.
tests/            # Unit + web + integration tests (135).
demo/run_demo.py  # End-to-end demonstration.
scripts/          # capture_chain_evidence.py
evidence/         # chain_evidence.json — read-only proof, reproducible.
docs/             # Integration status, architecture notes, Mission 5 review.
```

---

## Testing

```bash
# Offline: everything except the live chain reads
TAPEJUDGE_SKIP_CHAIN=1 python3 -m unittest discover -s tests

# Full, including read-only X Layer checks (needs network)
python3 -m unittest discover -s tests

# Lint
python3 -m ruff check .
```

The live tests are read-only and gated on `TAPEJUDGE_SKIP_CHAIN`. They perform
`eth_call` against the real X Layer contracts and assert that the local
evaluator agrees with the chain. They never send a transaction.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TAPEJUDGE_DB` | `:memory:` | SQLite path. Use a file for persistence. |
| `TAPEJUDGE_HOST` / `TAPEJUDGE_PORT` | `127.0.0.1` / `8080` | Dev server bind. |
| `TAPEJUDGE_SECRET` | a per-process random value | Flask session key. Set a real one in production; the app refuses a fixed default. |
| `TAPEJUDGE_SKIP_CHAIN` | unset | `1` skips the live chain tests. |
| `TAPEJUDGE_INSECURE_COOKIES` | unset | `1` drops the `Secure` cookie flag (local http demo only). |
| `TAPEJUDGE_ALLOW_DEV_SECRET` | unset | `1` allows the fixed dev secret outside testing (local demo only). |
| `TAPEJUDGE_DISABLE_RATELIMIT` | unset | `1` disables the write-endpoint rate limiter. |

---

## Status and limits

This is a functional MVP, not a production service.

* **Accounts and authorisation are implemented (Mission 6).** Organisers,
  entrants and judges sign in with an email and password (scrypt-hashed); every
  write requires a session and a CSRF token; a judge scores only under the label
  bound to their account; an organiser acts only on their own competition; and
  public verification stays anonymous. What is **not** proven is fairness: the
  app shows *which account* submitted a score, not whether that judge was fair.
  See `docs/MISSION6_SECURITY_REVIEW.md`.
* **The MVP caps a panel at 25 judges** and uses integer scores.
* **Publication is prepared, not executed.** Wiring a signer is a deliberate,
  separate step that requires a human decision about keys.
* **SQLite** is fine for one instance; a multi-instance deployment needs a
  shared store and a shared rate limiter.

The Mission 5 independent review and its fixes are in `docs/MISSION5_SECURITY_REVIEW.md`;
the Mission 6 authentication and authorisation work is in
`docs/MISSION6_SECURITY_REVIEW.md` (with the threat model, vulnerability
register, regression evidence, release readiness and deployment plan alongside
it). See `docs/` for the integration status and the evidence behind the chain
numbers.
