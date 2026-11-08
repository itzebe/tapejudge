# TapeJudge — evidence register (hackathon judges)

Every claim TapeJudge makes, the exact way to reproduce it, and what it does
**not** prove. Captured **2026-11-08** against the public deployment. Nothing
here is an estimate; each row was executed on this date.

---

## A. X Layer / TapeOut facts (read-only, reproducible)

All values were read with `eth_call` against X Layer mainnet, chain **196**. The
raw capture is `evidence/chain_evidence.json`; re-capture with
`python scripts/capture_chain_evidence.py`.

| Fact | Value | How to reproduce |
|---|---|---|
| Chain id | `196` | `cast chain-id --rpc-url https://rpc.xlayer.tech` |
| Factory (TapeOut) | `0x1f09DAeFA827f02CBb40967cc91b259763760761` | `cast call <factory> 'cpuCount()(uint256)'` |
| Processor 0 | `0x839bdd6fa7a66416a609a735e11de5411b98574e` | `cast call <factory> 'cpuAt(uint256)(address)' 0` |
| Processor count | `415` | same call |
| `nextId` | `103` | `cast call <processor> 'nextId()(uint256)'` |
| Transistor token | `0xbe0eee424c49d44592e4d498740f5b031ef8e26d` | `cast call <processor> 'transistors()(address)'` |
| `TAPEOUT_FEE` | `0.0013 OKB` | `cast call <processor> 'TAPEOUT_FEE()(uint256)'` |
| `tapeout` selector | `0x7bd3ac1d` (`tapeout(bytes,uint32,uint32)`) | `cast sig 'tapeout(bytes,uint32,uint32)'`; pinned by `assert selector(...) == TAPEOUT_SELECTOR` in `tapejudge/xlayer.py` |
| Confirmation primitive | a nonexistent circuit reverts `no circuit`; a real circuit returns its netlist | `cast call <processor> 'netlist(uint256)(bytes)' <id>` |

**Cross-check.** An independent public project, `tapeout-xlayer-integrity-lens`,
agrees on the factory, processor and transistor addresses. Two independent
readings of the same chain state is the strongest available check short of the
TapeOut team's own docs.

**What this does not prove.** It does not prove a `tapeout` was ever *sent* by
TapeJudge. That would require broadcasting a signed transaction, which the app
deliberately does not do (it holds no key).

---

## B. The product's core claim

> The ranking is computed by re-running the frozen circuit from stored inputs,
> never stored as a number.

Reproduce: open `/c/<id>/verify` on the live app. The page recompiles the rule,
re-packs the stored scores, re-runs the evaluator, and shows the exact on-chain
call. The offline equivalent:

```bash
python demo/run_demo.py     # prints "frozen rule intact: True"
```

Regression: `tests/test_publication.py`, `tests/test_scoring.py`,
`tests/test_netlist.py`.

---

## C. Security properties (live)

| Property | Probe | Observed |
|---|---|---|
| CSRF on every write | `POST /api/competitions` with no token | `401` anonymous / `403` authenticated-but-tokenless |
| Anonymous write refused | `POST /c/<id>/publish` with no session | `302 → /login`, no state change |
| Login throttle | 12 failed logins, one ip+email | bounded (limit 10 / 300 s) |
| Ownership gate | a foreign competition id | same `404` as an unknown id |
| Security headers | `curl -D -` | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, scoped CSP, `frame-ancestors 'none'` |
| API responses not cached | `curl -D - /api/integration` | `Cache-Control: no-store` |
| No secret in errors | `tests/test_security.py` (sanitiser) | upstream URLs collapsed, no stack |

Full findings: `docs/TAPEJUDGE_VULNERABILITY_REGISTER.md`,
`docs/TAPEJUDGE_THREAT_MODEL.md`.

---

## D. Accessibility (live)

`node scripts/a11y_audit.mjs` over the public pages at 390 / 768 / 1440 px:

| Width | Overflow | Small non-prose targets | Low-contrast text | Unnamed controls |
|---|---|---|---|---|
| 390 | 0 | 0 | 0 | 0 |
| 768 | 0 | 0 | 0 | 0 |
| 1440 | 0 | 0 | 0 | 0 |

Screenshots: `evidence/screenshots/{overview,results,verify,publish,rubric}.png`.

---

## E. Test suite

```bash
python3 -m unittest discover -s tests
```

**246 tests, OK, 8 skipped.** The 8 skips are the live-network tests that run
only when `TAPEJUDGE_LIVE=1`; they are skipped, not passed. `python3 -m ruff
check .` is clean.

---

## F. Honest boundaries

- **Publication is prepared, not sent** — the app holds no key.
- **Fairness is not proven** — the rule and the arithmetic are, the judge is not.
- **Demo data is `SYNTHETIC`** and labelled as such everywhere.
- **No transaction was ever sent** while building or demonstrating TapeJudge.
- **Hosting is not durable** — see `docs/TAPEJUDGE_PRODUCTION_DEPLOYMENT_PLAN.md`.
