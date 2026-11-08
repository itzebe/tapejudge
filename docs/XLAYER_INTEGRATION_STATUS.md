# X Layer integration status

Research date: **2026-11-08** (revalidated; the previous capture was 2026-11-07).
All chain facts below were read live with read-only `eth_call` against
`https://rpc.xlayer.tech` (chain id 196). The raw capture is
`evidence/chain_evidence.json`, produced by
`python scripts/capture_chain_evidence.py`. Nothing here was sent as a
transaction, and no wallet or key was used.

## What was verified (read-only), 2026-11-08

| Fact | Value at capture | Source |
|---|---|---|
| Chain id | 196 | `eth_chainId` |
| Block number | 75349229 | `eth_blockNumber` |
| Factory | `0x1f09DAeFA827f02CBb40967cc91b259763760761` | re-read |
| `cpuCount()` | **415** | `eth_call` on the factory |
| `cpuAt(0)` | `0x839bdd6fa7a66416a609a735e11de5411b98574e` | `eth_call` |
| Processor `nextId()` | **103** | `eth_call` |
| Processor `transistors()` | `0xbe0eee424c49d44592e4d498740f5b031ef8e26d` | `eth_call` (the ERC-20 a tapeout burns) |
| `TAPEOUT_FEE()` | `1300000000000000` wei = **0.0013 OKB** | `eth_call` |
| Circuit beacon | `0xf70d1ed4f62cf3780157b0b421b7e2f45bd0991c` | re-read |
| Beacon `owner()` | `0x0000…0000` | `eth_call` — the circuit layer is frozen/ownerless |
| Beacon `implementation()` | `0x977f217887e085d298cb3819cdad5a0ee35f29b2` | `eth_call` |

The block advanced from 75,326,296 (2026-11-07) to 75,349,229 (2026-11-08) — a
live chain, re-read rather than reused. `cpuCount()` is 415 and processor 0's
`nextId()` is 103.

**Independent corroboration of the addresses.** A separate X Layer TapeOut
hackathon project, *TapeOut Integrity Lens*
(<https://github.com/livastic-vm5-primary-20260911/tapeout-xlayer-integrity-lens>),
published pinned-block evidence for the same mainnet contracts
(`evidence/tapeout-cpu0-block73947001.json`): factory
`0x1f09daefa827f02cbb40967cc91b259763760761`, processor-0 circuits
`0x839bdd6fa7a66416a609a735e11de5411b98574e`, `tapeoutFee`
`1300000000000000`, transistor token `0xbe0eee424c49d44592e4d498740f5b031ef8e26d`,
circuit market `0xd89f358c48a7B632c9845af2a02A32eB90DD75DB`. Our live reads
agree with it field for field (its block was 73,947,001; ours is later and the
counters have moved on, as expected). This is a **second, independent source**
for the addresses, not a copy of our own record.

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
| `transistors()` | computed | returns the burn token |

## Local evaluator vs. the chain

TapeJudge's local evaluator is the same TAP-02 semantics the on-chain processor
implements. `XLayerClient.verify_circuit_against_chain` reads an existing circuit
(`eval`) and compares its output to the local evaluator for the same input bytes.

Captured result for circuit #1 (a small combinational circuit), inputs
`0x00, 0x01, 0x02, 0x03`:

| Input | Local | Chain | Match |
|---|---|---|---|
| `0x00` | `0x00` | `0x00` | ✓ |
| `0x01` | `0x01` | `0x01` | ✓ |
| `0x02` | `0x01` | `0x01` | ✓ |
| `0x03` | `0x00` | `0x00` | ✓ |

## The publication transaction, precisely

Answering the nine questions a real publication raises:

1. **What is the transaction?** `tapeout(bytes netlist, uint32 nIn, uint32 nOut)`
   on the processor, selector `0x7bd3ac1d`, with `msg.value` equal to the
   processor's `TAPEOUT_FEE` exactly (TAP-02 §6).
2. **Which contract receives it?** The processor at
   `0x839bdd6fa7a66416a609a735e11de5411b98574e` (processor index 0 of the
   factory at `0x1f09…0761`).
3. **What data is committed?** The frozen netlist bytes plus its declared input
   and output widths. The netlist's SHA-256 is the commitment; it is what the app
   compares on confirmation.
4. **What does it cost?** `TAPEOUT_FEE` = 0.0013 OKB (read live) as `msg.value`,
   plus gas, plus the **burn** of the circuit's top-level NAND+LATCH count in
   transistor tokens (`0xbe0eee…26d`) held by the signing wallet (TAP-02 §6). For
   the synthetic demo rubric (1,384 gates) that is 1,384 transistor tokens.
5. **What state proves success?** A successful receipt (status `0x1`) **and** a
   new circuit at `nextId()` whose `netlist(id)` returns exactly the frozen bytes.
6. **How can anyone verify it?** Independently, with a free read: `netlist(id)`
   must hash to the recorded netlist SHA-256, and `eval(id, inputs)` must agree
   with the local evaluator. The app's **Confirm publication** form does exactly
   this read.
7. **How is a circuit associated with a competition?** The app records the
   `chain_id`, `processor` and `circuit_id` in the competition row when the
   publication is prepared, and the `tx_hash` only when confirmation matches. The
   competition's `publication.circuit_id` is the association.
8. **What if publication fails or is repeated?** A reverted receipt, a mismatched
   netlist or a missing circuit is reported honestly and the competition stays
   *prepared* — never *published*. A repeat attempt burns another fee and another
   gate-count of transistors and creates a *new* circuit id; the old one is not
   replaced. The app never resubmits on its own.
9. **Can the published rules be retrieved and reproduced?** Yes. The netlist is
   public via `netlist(id)`; the rubric digest is stored off-chain and the
   mapping from netlist to ranking is the TAP-02 evaluator, which anyone can
   re-run.

## Publication: prepared, confirmed only by evidence

`prepare_tapeout` builds the exact unsigned transaction:

* **to**: the processor (`0x839bdd6f…574e`)
* **chainId**: 196
* **data**: `0x7bd3ac1d` + ABI-encoded `(bytes netlist, uint32 nIn, uint32 nOut)`
* **value**: the fee, when the processor exposes a getter

`confirm_publication` (new, 2026-11-08) is the read half: given a transaction
hash, it reads the receipt and the circuit back and moves the competition to
`onchain_verified` **only** when the receipt succeeded and the on-chain netlist
equals the frozen one. `scripts/prepare_publication.py` prints the whole
human-readable package (target, value, calldata, gas floor, the exact `cast`
commands to send and to confirm).

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
python scripts/prepare_publication.py <db> <competition_id>   # the human package
python3 -m unittest tests.test_integration tests.test_publication
```

All are read-only. The numbers in this file will move as the chain advances; the
point is that they can be re-read and checked, not that they are frozen.
