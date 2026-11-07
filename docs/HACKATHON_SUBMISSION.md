# TapeJudge — hackathon submission kit

Everything here is fact about the project; edit the wording to your voice before
posting. No engagement numbers, testimonials or metrics are invented.

---

## One-paragraph pitch

Judged competitions have an arithmetic problem: when a ranking is disputed,
nobody can reproduce the maths, because the scoring rule lives in a spreadsheet
and the totals are whatever the organiser says they are. TapeJudge compiles the
scoring rule into a TapeOut circuit, freezes and hashes it, and computes the
ranking **by re-running that circuit from the stored inputs** every time it is
requested — so anyone can re-run it for free and get the same numbers. It runs
on X Layer, and it is a working app, not a slide.

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
`TAPEOUT_FEE`) with read-only `eth_call` and reports the exact wallet
requirements (the OKB fee **and** the transistor tokens a `tapeout` burns). This
is the point: the rule is not "a JSON blob we promise not to edit" — it is a
circuit that has a canonical, public, reproducible form.

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
3. Open **Publish** → the honest publication state and the unsigned transaction.

## Honest boundaries (say these out loud — they are a feature)

- **Publication is prepared, not sent.** The app holds no key; it builds the
  unsigned transaction and stops.
- **Fairness is not proven.** The app makes the *rule* fixed and the *arithmetic*
  reproducible; it shows which account scored, not whether a judge was fair.
- **Demo data is labelled `SYNTHETIC`** everywhere it appears.
- **No transaction was ever sent** in building or demonstrating this.

## Links

- Repository: <https://github.com/itzebe/tapejudge>
- Live app: `https://work-1-gvcyygdjrlurcmdz.prod-runtime.all-hands.dev/`
  (replace with the final host when published)
- Chain evidence (read-only, reproducible): `evidence/chain_evidence.json`,
  `docs/XLAYER_INTEGRATION_STATUS.md`
- Security reviews: `docs/MISSION5_SECURITY_REVIEW.md`,
  `docs/MISSION6_SECURITY_REVIEW.md`, `docs/TAPEJUDGE_THREAT_MODEL.md`

## Suggested post angle (edit to your voice)

> A judge's spreadsheet and a frozen circuit both produce a number. Only one of
> them can be re-run by a stranger.
>
> We built TapeJudge: the scoring rule is a TapeOut circuit on X Layer, and the
> ranking is recomputed from stored inputs every time — never stored. Disagreements
> become facts you can check, not arithmetic you have to trust.
