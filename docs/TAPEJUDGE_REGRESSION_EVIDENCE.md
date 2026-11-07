# TapeJudge — Regression Evidence (Mission 5)

Research date **2026-11-07**. All commands were run in `/workspace/tapejudge`
on the reviewed working tree (baseline `f3c229a`). Every result below is a real
capture, not a claim.

---

## 1. Test suites

### Offline (no network)

```bash
TAPEJUDGE_SKIP_CHAIN=1 python3 -m unittest discover -s tests
```

```
Ran 135 tests in 2.275s

OK (skipped=7)
```

The 7 skips are the live-chain tests, gated on `TAPEJUDGE_SKIP_CHAIN` (see §1b).

### Full, including live X Layer reads

```bash
python3 -m unittest discover -s tests
```

```
Ran 135 tests in 37.726s

OK
```

**Baseline comparison:** the suite had **109 tests (7 skipped)** before this
mission. It now has **135** — the 26 added tests are
`tests/test_hardening.py` (25 defect-regression tests + 1 stylesheet regression).

### 1b. The skipped tests are real, and they pass against the chain

```bash
python3 -m unittest tests.test_chain tests.test_integration
```

```
Ran 12 tests in 37.021s

OK
```

These perform read-only `eth_call` against the live X Layer contracts (chain 196)
and assert the local TAP-02 evaluator agrees with the chain. No transaction is
sent.

---

## 2. Lint

```bash
python3 -m ruff check .
```

```
All checks passed!
```

One `PIE810` (merge `endswith`) and one `S105` were introduced by this mission
and fixed; the tree is clean.

---

## 3. Dependency audit

```bash
python3 -m pip_audit
```

```
No known vulnerabilities found
```

The project depends on Flask only (`pyproject.toml`). The rate limiter added in
this mission uses the standard library, so no dependency was added.

---

## 4. End-to-end demo

```bash
TAPEJUDGE_SKIP_CHAIN=1 python3 demo/run_demo.py
```

Tail of the output:

```
 1 team-synthetic-aurora        139  [78, 48, 13]
 2 team-synthetic-cirrus        114  [72, 30, 12]
 3 team-synthetic-borealis      105  [54, 42, 9]
frozen rule intact: True
independent ranking: [(1, 'team-synthetic-aurora', 139), ...]
published on chain : False (local_only)
All data above is SYNTHETIC. No transaction was sent.
```

The workflow (create → rubric → freeze → entries → scores → results → verify)
runs end to end and the ranking is reproduced independently.

---

## 5. Adversarial HTTP probe (27 assertions)

Run against a live server on `http://localhost:12000` with a throwaway DB:

```
[PASS] health 200 200
[PASS] panel mismatch -> 4xx not 500 400
[PASS] no-rubric results page 200 200
[PASS] no-rubric verify page 200 200
[PASS] no-rubric results API 4xx not 500 400
[PASS] unknown comp page 404 404
[PASS] unknown comp API 404 404
[PASS] bad method 405 405
[PASS] oversized body 413 413
[PASS] X-Content-Type-Options
[PASS] X-Frame-Options DENY
[PASS] CSP present
[PASS] API no-store
[PASS] XSS escaped
[PASS] traversal '../../etc/passwd' 404/400 404
[PASS] traversal '..%2f..%2fetc%2fpasswd' 404/400 404
[PASS] traversal 'comp_zzz' 404/400 404
[PASS] bool score rejected 400 400
[PASS] float score rejected 400 400
[PASS] valid score 201 201
[PASS] duplicate score 400 (not 500) 400
[PASS] identical resubmit idempotent 201/400 400
[PASS] double freeze 4xx 400
[PASS] frozen rubric replace 4xx 400
[PASS] unknown entry 4xx 400
[PASS] off-panel judge 4xx 400
[PASS] malformed JSON 4xx 400
```

**27/27 PASS.** The server access log recorded **0** HTTP 500 responses across
the whole probe session.

### 5b. SQL injection probe

Four injection payloads were posted as a competition name
(`'; DROP TABLE competitions; --`, `Robert'); DROP TABLE evaluations;--`,
`1 OR 1=1`, `%27%20OR%201=1`). All were stored as inert text (201), the
`competitions` list still answered 200, and no table was affected — queries are
parameterised.

### 5c. Secret leakage

`GET /api/competitions/<id>` was checked for the internal `commit_key`:
**absent**. The audit trail contains the public `commitment` (expected) but not
`commit_key`. `SECRET_KEY` is never serialised.

---

## 6. Concurrency

```bash
python3 -m unittest tests.test_hardening.TestEvaluationRace
```

```
Ran 3 tests ... OK
```

The key case starts **16 threads** that submit the *same* `(judge, entry)` to a
file-backed SQLite database. Result: exactly **1** `ok`, **15** clean conflicts,
**0** unexpected exceptions, **1** stored row.

---

## 7. Rendered-pixel verification (headless Chromium via CDP)

Measured `document.documentElement.scrollWidth − clientWidth` (horizontal
overflow) on 5 pages at 3 viewports.

**Before the CSS fix:**

| Page | 390px | 768px | 1440px |
|---|---|---|---|
| `/` | **91** | 0 | 0 |
| `/results` | **27** | 0 | 0 |
| `/verify` | **97** | **29** | 0 |
| `/judge` | **27** | 0 | 0 |
| `/rubric` | **27** | 0 | 0 |

**After the fix:**

| Page | 390px | 768px | 1440px |
|---|---|---|---|
| `/` | 0 | 0 | 0 |
| `/results` | 0 | 0 | 0 |
| `/verify` | 0 | 0 | 0 |
| `/judge` | 0 | 0 | 0 |
| `/rubric` | 0 | 0 | 0 |

**0 horizontal scroll at every width on every page.** Screenshots were captured
for each viewport (Chromium, headless).

### 7b. Accessibility / semantics audit (DOM, 1280px)

Per page: `lang="en"`, a skip link, exactly one `h1`, 0 images without `alt`,
0 unlabelled inputs, 0 unnamed buttons, 0 empty-href links, and every `<th>`
carries `scope`. Clean on `/`, `/new`, `/results`, `/verify`, `/judge`,
`/rubric`, `/submit`.

### 7c. Colour contrast (WCAG 2.2 AA, computed)

| Pair | Ratio | Result |
|---|---|---|
| ink on bg | 17.49 | AA-normal |
| ink-2 on bg | 9.92 | AA-normal |
| ink-3 on bg | 4.67 | AA-normal |
| ink-3 on surface | 4.83 | AA-normal |
| accent on surface | 5.02 | AA-normal |
| warn on accent-soft | 5.39 | AA-normal |
| ok on ok-soft | 5.89 | AA-normal |
| err on err-soft | 7.01 | AA-normal |

All text/background pairs meet the 4.5:1 normal-text threshold.

---

## 8. Chain re-validation (read-only)

```bash
python3 scripts/capture_chain_evidence.py
```

Captured `2026-11-07T19:54:02Z`:

| Fact | Value | Method |
|---|---|---|
| Chain id | 196 | `eth_chainId` |
| Block number | 75312206 | `eth_blockNumber` |
| Factory | `0x1f09DAeFA827f02CBb40967cc91b259763760761` | re-read |
| `cpuCount()` | 415 | `eth_call` |
| `cpuAt(0)` | `0x839bdd6fa7a66416a609a735e11de5411b98574e` | `eth_call` |
| Processor `nextId()` | 103 | `eth_call` |
| Beacon `owner()` | `0x0000…0000` | `eth_call` (frozen/ownerless) |
| Beacon `implementation()` | `0x977f217887e085d298cb3819cdad5a0ee35f29b2` | `eth_call` |

The local evaluator matched the on-chain `eval` for circuit #1 on all four
sample inputs (`evidence/chain_evidence.json`). **Nothing was sent**; no wallet
or key was used.

---

## 9. What was **not** verified

- **No production deployment** was performed (out of scope; the mission is
  review + gate).
- **No load test.** Concurrency was exercised at 16 threads on SQLite, not at
  production scale.
- **No real wallet publication.** `prepare_tapeout` builds the unsigned call and
  stops; the actual `tapeout` transaction was not sent (it needs a funded signer).
- **No browser matrix beyond Chromium.** Firefox/Safari were not exercised.
- **No authenticated flow** — there is no authentication to test (residual A-01).
