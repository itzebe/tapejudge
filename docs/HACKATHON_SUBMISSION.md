# TapeJudge — hackathon submission kit

Everything here is fact about the project; edit the wording to your voice before
posting. No engagement numbers, testimonials or metrics are invented.

Research date: **2026-11-08**. The official requirements below were read from the
event page on that date.

---

## One-paragraph pitch

Judged competitions have an arithmetic problem: when a ranking is disputed,
nobody can reproduce the maths, because the scoring rule lives in a spreadsheet
and the totals are whatever the organiser says they are. TapeJudge compiles the
scoring rule into a TapeOut circuit, freezes and hashes it, and computes the
ranking **by re-running that circuit from the stored inputs** every time it is
requested — so anyone can re-run it for free and get the same numbers. The frozen
rules are TapeOut circuits on X Layer; the app itself is a working product, not a
slide.

---

## The problem, precisely

- A competition's result is only as trustworthy as its arithmetic, and the
  arithmetic is usually invisible.
- Once scores are tallied, a losing entrant has no way to check the total without
  trusting the organiser's spreadsheet.
- Existing "leaderboard" tools store a final number. A stored number cannot be
  independently recomputed; it can only be believed.

## What TapeJudge does

1. The organiser writes criteria (weight, max score), a panel size and a
   tie-break.
2. TapeJudge **compiles that rule into a TapeOut circuit** and shows its size and
   netlist hash.
3. The rule is **frozen** — the store refuses to replace a frozen rubric.
4. Judges commit integer scores; each is validated against the frozen rubric
   before it is stored.
5. The ranking is **computed by the frozen circuit**, never stored.
6. Anyone opens `/verify`, and TapeJudge recompiles the rule, re-packs the scores
   and re-runs the same evaluator, showing the exact call to reproduce it on
   chain.

## Why X Layer / TapeOut

The frozen rule is a **TapeOut circuit**, and the publication path is a real,
unsigned `tapeout(bytes,uint32,uint32)` call against the live X Layer processor.
The app reads the live processor registry (`cpuCount()`, `cpuAt(0)`, `nextId()`,
`TAPEOUT_FEE`, `transistors()`) with read-only `eth_call`, reports the exact
wallet requirements (the OKB fee **and** the transistor tokens a `tapeout`
burns), and can **confirm** a publication afterwards by reading the circuit back
and comparing it byte-for-byte with the frozen netlist. This is the point: the
rule is not "a JSON blob we promise not to edit" — it is a circuit with a
canonical, public, reproducible form.

## Official requirements (read from the event page, 2026-11-08)

Source: <https://web3.okx.com/xlayer/build-x-series> (X Layer TapeOut Accelerate).

- **Deadline:** December 7, 2026, 23:59 UTC. The page itself calls this
  **provisional** — "the official event page will confirm the final date".
- **Submission:** the submission Google Form.
- **Must build in the TapeOut ecosystem.** Exact wording: *"Projects must build
  within the TapeOut ecosystem and either use TapeOut circuits, contracts, or
  onchain data on X Layer, or run on X Layer DeWEB."* So the gate is an
  either/or: circuits/contracts/onchain-data **or** a DeWEB deployment. TapeJudge
  satisfies the first branch (real `tapeout`/`eval` calls against the live
  processor).
- **Access link required:** "Websites, apps and tools must provide an access
  link and explain which TapeOut ecosystem projects or features they use."
- **Circuit projects** must submit the processor contract address and circuit ID.
- **Named ecosystem capabilities** (from the *Ecosystem Integration* criterion):
  DeWEB, FabX, TapeCode, TapeKit, TapeSend. The page is explicit that *"the use
  of any specific project or tool is not required"* — so naming them is not a
  scoring requirement, and TapeJudge does not claim to use any it has not
  verified.
- **Description must cover:** the product/service, target users and how to reach
  them, the proposed revenue model, and why users would pay and keep using it.
- **Demo video:** recommended no more than three minutes; cover the problem, core
  features, target users and business model, and how TapeOut is used.
- **Judging:** User Value & Commercial Potential · Innovation · Ecosystem
  Integration · Product Readiness & UX · Demo Video (official X Layer + TapeOut
  teams jointly).

## How TapeJudge maps to the requirements

| Requirement | TapeJudge |
|---|---|
| Uses TapeOut on X Layer, with addresses | Processor `0x839bdd6fa7a66416a609a735e11de5411b98574e` (processor 0 of factory `0x1f09DAeFA827f02CBb40967cc91b259763760761`), chain 196; selectors and reads verified live (`docs/XLAYER_INTEGRATION_STATUS.md`). |
| Access link | <https://tapejudge.vercel.app> — the production deployment (Vercel + Neon Postgres). |
| Processor address + circuit ID | Processor above. A circuit ID is only meaningful once a real `tapeout` has been sent; the app prepares and can confirm one, and it never claims a circuit ID that does not exist. |
| Target users | Hackathon and grant organisers, DAO grant rounds, design/art jams, academic prize committees — anyone whose ranking is disputed. |
| Revenue model | Free to verify forever; a paid organiser tier (private rounds, branded results, API access) once the free tier proves the point. |
| Demo video | `demo/tapejudge-final-demo.mp4` (2:00). Recorded shot list and narration in `docs/FINAL_DEMO_VIDEO.md`; planning script in `docs/FINAL_DEMO_SCRIPT.md`. |

## Demo script (about 5 minutes, all offline-safe)

```bash
python demo/run_demo.py            # full workflow, in-memory, SYNTHETIC
```

It prints, in order: sign-up, create, configure rubric (with the circuit size and
netlist hash), freeze (and a rejected attempt to replace the frozen rule),
prepare publication (the unsigned boundary), submit entries, a refused
cross-account action, committed (immutable) scores, the computed ranking, and an
independent re-run that confirms the frozen rule is intact.

Then, in the live app:

1. Open the SYNTHETIC demo competition → **Results** (the ranking, subtotals and
   incomplete-panel warnings).
2. Open **Verify** → the reproduction and the exact chain call.
3. Open **Publish** → the honest publication state, the unsigned transaction, and
   the **Confirm publication** form.

## Honest boundaries (say these out loud — they are a feature)

- **Publication is prepared, not sent.** The app holds no key; it builds the
  unsigned transaction and stops. A published circuit exists only after the
  operator signs and broadcasts it, and the app confirms it by reading the chain.
- **Fairness is not proven.** The app makes the *rule* fixed and the *arithmetic*
  reproducible; it shows which account scored, not whether a judge was fair.
- **Demo data is labelled `SYNTHETIC`** everywhere it appears.
- **No transaction was ever sent** in building or demonstrating this.
- **Hosting is durable.** The app runs on Vercel (`tapejudge.vercel.app`) backed
  by a Neon Postgres store, so records survive a cold start. The earlier work-host
  URL was a sandbox and is no longer the access link.

## Links

- Repository: <https://github.com/itzebe/tapejudge>
- Live app: <https://tapejudge.vercel.app>
- Chain evidence (read-only, reproducible): `evidence/chain_evidence.json`,
  `docs/XLAYER_INTEGRATION_STATUS.md`
- Publication package: `scripts/prepare_publication.py`
- Security reviews: `docs/MISSION5_SECURITY_REVIEW.md`,
  `docs/MISSION6_SECURITY_REVIEW.md`, `docs/TAPEJUDGE_THREAT_MODEL.md`,
  `docs/TAPEJUDGE_VULNERABILITY_REGISTER.md`

## Suggested post angle (edit to your voice)

> A judge's spreadsheet and a frozen circuit both produce a number. Only one of
> them can be re-run by a stranger.
>
> We built TapeJudge: the scoring rule is a TapeOut circuit on X Layer, and the
> ranking is recomputed from stored inputs every time — never stored. Disagreements
> become facts you can check, not arithmetic you have to trust.

---

## Submission form — ready answers (edit to your voice)

**Project name:** TapeJudge

**One-line:** A competition's scoring rule becomes a frozen TapeOut circuit on X
Layer, and the ranking is recomputed from the stored scores every time it is
requested — never stored.

**What it is (2–3 sentences):** TapeJudge runs judged competitions whose results
anyone can reproduce. The organiser compiles the scoring rule into a TapeOut
circuit and freezes it; judges commit scores under their own accounts; the
ranking is produced by re-running the frozen circuit from the stored inputs, and
a public Verify page re-runs it and prints the exact on-chain call.

**Target users and how to reach them:** Hackathon and grant organisers, DAO grant
rounds, design and art jams, and academic prize committees — anyone whose ranking
can be disputed. Reach them where they already run competitions (Gitcoin-style
grant rounds, Devpost, Discord community jams) with the free Verify page as the
hook.

**Revenue model and why users pay:** Verification is free forever. Organisers pay
for a private-round tier (branded result pages, API access, archived rounds) once
the free tier has shown the results are reproducible. They pay because a
reproducible result protects them from disputes they currently settle by hand.

**Which TapeOut feature is used:** The scoring rule is compiled to a TapeOut
circuit; the app reads the live processor registry (`cpuCount`, `cpuAt`,
`nextId`, `TAPEOUT_FEE`, `transistors`) and prepares the unsigned
`tapeout(bytes,uint32,uint32)` call, and it can confirm a publication by reading
the circuit back and comparing it byte-for-byte with the frozen netlist.

**Processor contract address:** `0x839bdd6fa7a66416a609a735e11de5411b98574e`

**Circuit ID:** none yet — a circuit ID is only real after the prepared `tapeout`
is signed and broadcast. The app never claims one that does not exist.

**Access link:** <https://tapejudge.vercel.app>

**Demo video:** `<paste the recorded link>`
