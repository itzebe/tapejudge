# TapeJudge — final demo video script

> **This is the planning script.** The recording has since been produced; the
> authoritative document — the shot list that was actually recorded, the
> narration verbatim, the honesty notes and how to reproduce it — is
> [`docs/FINAL_DEMO_VIDEO.md`](FINAL_DEMO_VIDEO.md). The video is
> [`demo/tapejudge-final-demo.mp4`](../demo/tapejudge-final-demo.mp4) (2:00).

The hackathon recommends a demo of **no more than three minutes** covering: the
problem, a demonstration of the core features, the target users and business
model, and how TapeOut is used. This script is written to that brief. Record it
on the live app; keep the SYNTHETIC demo competition on screen so nothing looks
like real customer data.

Total target: **2:40**.

---

## Shot list

| # | Time | Shot | What is on screen | Voice-over (say it plainly) |
|---|---|---|---|---|
| 1 | 0:00–0:15 | The problem | A spreadsheet-style score table (any slide) | "Every judged competition ends the same way: a number you have to trust. If you lose, you cannot check the arithmetic — the rule is in the organiser's spreadsheet." |
| 2 | 0:15–0:30 | The idea | TapeJudge home page | "TapeJudge turns the scoring rule into a circuit, freezes it, and computes the ranking by re-running that circuit from the stored scores — never a stored total." |
| 3 | 0:30–0:55 | Configure the rule | `/c/<id>/rubric` | "Here the organiser sets criteria, weights and a panel size. The moment the rule is set, TapeJudge compiles it into a TapeOut circuit and shows the gate count and the netlist hash." |
| 4 | 0:55–1:15 | Freeze | Publish page → Freeze, then a rejected attempt to change the rule | "Freezing is irreversible. Watch: trying to change the rule now is refused by the store, not by a promise." |
| 5 | 1:15–1:40 | Score | Judge page, submit scores, a refused cross-account action | "Judges commit integer scores under their own account. Each is validated against the frozen rule before it is stored. A different account cannot touch someone else's score." |
| 6 | 1:40–2:00 | Results | Results page | "The ranking is computed, not stored. Subtotals and the tie-break come from the circuit." |
| 7 | 2:00–2:20 | Verify (the point) | `/c/<id>/verify` | "Anyone opens Verify. TapeJudge recompiles the rule, re-packs the scores and re-runs the same evaluator — and prints the exact on-chain call to reproduce it. Disagreement becomes a fact you can check." |
| 8 | 2:20–2:35 | TapeOut on X Layer | Publish page: processor address, live reads, the unsigned transaction, the Confirm form | "The rule is a TapeOut circuit on X Layer, chain 196, against processor 0x839b…574e. The app reads the live processor, prepares the exact unsigned `tapeout` call — the OKB fee and the transistors to burn — and can confirm a real publication by reading the circuit back." |
| 9 | 2:35–2:40 | Close | Title card | "TapeJudge. A rule a stranger can re-run." |

---

## Preparation

1. Confirm the app is healthy — locally (`curl -s http://127.0.0.1:12000/api/health`)
   or on the production host (`curl -s https://tapejudge.vercel.app/api/health`);
   both answer `{"chain_reachable":true,"ok":true}`.
2. Open the **SYNTHETIC — Demo Judging Round** competition on
   <https://tapejudge.vercel.app> (id `comp_563ebff5be18`). Do not create new
   data live if the recording is one take; the demo competition already has a
   frozen rule, entries and scores.
3. Have these tabs open before you hit record (the demo competition has no
   separate overview route; `/results` is the public hub):
   - `/c/comp_563ebff5be18/results` (public ranking — the hub)
   - `/c/comp_563ebff5be18/verify` (independent verification)
   - `/c/comp_563ebff5be18/rubric` (frozen rule; organiser view)
   - `/c/comp_563ebff5be18/judges` (panel)
   - `/c/comp_563ebff5be18/publish` (honest publication state)
4. If the chain is briefly unreachable, the publish page says so honestly — that
   is a feature. Do not hide it; if it happens, re-record that shot when the RPC
   answers.

## Offline backup (if the live app or the RPC is unavailable)

```bash
python demo/run_demo.py
```

It runs the whole workflow in memory and prints every step, including the
independent re-run that proves the frozen rule is intact. Record the terminal if
the browser is unavailable; the same claims are shown.

## What NOT to do

- Do not claim a transaction was sent, a circuit ID that does not exist, or a
  customer.
- Do not show the words "swap", "token" or a fake dashboard metric.
- Do not speed through the Verify shot — it is the reason the product exists.
