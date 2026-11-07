# X Layer integration status

Research date: **2026-11-07**. All chain facts below were read live with
read-only `eth_call` against `https://rpc.xlayer.tech` (chain id 196). The raw
capture is `evidence/chain_evidence.json`, produced by
`python scripts/capture_chain_evidence.py`. Nothing here was sent as a
transaction, and no wallet or key was used.

## What was verified (read-only)

| Fact | Value at capture | Source |
|---|---|---|
| Chain id | 196 | `eth_chainId` |
| Block number | 75308913 | `eth_blockNumber` |
| Factory | `0x1f09DAeFA827f02CBb40967cc91b259763760761` | Mission 3 record, re-read |
| `cpuCount()` | **415** | `eth_call` on the factory |
| `cpuAt(0)` | `0x839bdd6fa7a66416a609a735e11de5411b98574e` | `eth_call` |
| Processor `nextId()` | **103** | `eth_call` |
| Circuit beacon | `0xf70d1ed4f62cf3780157b0b421b7e2f45bd0991c` | Mission 3 record, re-read |
| Beacon `owner()` | `0x0000…0000` | `eth_call` — the circuit layer is frozen/ownerless |
| Beacon `implementation()` | `0x977f217887e085d298cb3819cdad5a0ee35f29b2` | `eth_call` |

`cpuCount()` moved from 414 (Mission 3) to 415, which is expected for a live
processor registry: it means the factory is live and the count grows as
processors are registered. `nextId()` on processor 0 is 103, up from 102 — one
more circuit was taped out since Mission 3.

## Selector verification

The four-byte selectors are computed with the vendored keccak and checked
against the TAP-02 constant at import time (`assert` in `tapejudge/xlayer.py`):

| Function | Selector | Check |
|---|---|---|
| `tapeout(bytes,uint32,uint32)` | `0x7bd3ac1d` | matches TAP-02 |
| `eval(uint256,bytes)` | `0x934d06ea` | matches TAP-02 |
| `circuitInfo(uint256)` | `0x084d60f1` | computed |
| `netlist(uint256)` | `0x3fc4be56` | computed |
| `nextId()` | `0x61b8ce8c` | computed |
| `cpuCount()` | `0xa94da8a7` | computed |
| `cpuAt(uint256)` | `0x4bc7cbbd` | computed |

## Local evaluator vs. the chain

TapeJudge's local evaluator is the same TAP-02 semantics the on-chain processor
implements. The cross-check in `XLayerClient.verify_circuit_against_chain` reads
an existing circuit (`eval`) and compares its output to the local evaluator for
the same input bytes.

Captured result for circuit #1 (a small combinational circuit), inputs
`0x00, 0x01, 0x02, 0x03`:

| Input | Local | Chain | Match |
|---|---|---|---|
| `0x00` | `0x00` | `0x00` | ✓ |
| `0x01` | `0x01` | `0x01` | ✓ |
| `0x02` | `0x01` | `0x01` | ✓ |
| `0x03` | `0x00` | `0x00` | ✓ |

## Publication: prepared, not sent

`prepare_tapeout` builds the exact unsigned transaction:

* **to**: the processor (`0x839bdd6f…574e`)
* **chainId**: 196
* **data**: `0x7bd3ac1d` + ABI-encoded `(bytes netlist, uint32 nIn, uint32 nOut)`
* **value**: the fee, when the processor exposes a getter

The deployed processor **does** answer a `TAPEOUT_FEE()` getter (this is not in
the TAP-02 spec, but it is live): the captured value is
`1300000000000000` wei = **0.0013 OKB** per tapeout. When a processor does not
expose the getter, the fee is reported as *unknown*, never `0`.

**The application never sends this transaction.** It stops at the boundary and
states plainly that the organiser's wallet must authorise it. This is a product
decision: the app holds no key and cannot sign.

## What was *not* done (and why)

* **No transaction was sent.** Writing to the factory/processor requires a funded
  signer; the mission is read-only. Publishing is therefore reported as
  `onchain_pending_signature`, and the verify page never claims an on-chain
  circuit when there is none.
* **No wallet was connected**, no key requested, no funds spent.
* The X Layer RPC (`rpc.xlayer.tech`) answered every read; a failure would have
  degraded the app to an honest "unknown" state rather than a fabricated value.

## Reproducing this document

```bash
python scripts/capture_chain_evidence.py     # rewrites evidence/chain_evidence.json
python3 -m unittest tests.test_integration   # the live read-only checks
```

Both are read-only. The numbers in this file will move as the chain advances;
the point is that they can be re-read and checked, not that they are frozen.
