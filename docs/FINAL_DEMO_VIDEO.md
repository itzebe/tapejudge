# TapeJudge — final demo video

**File:** [`demo/tapejudge-final-demo.mp4`](../demo/tapejudge-final-demo.mp4)
**Length:** 2:15 (135.5 s) · **Video:** 1280×712 H.264 · **Audio:** AAC mono 22.05 kHz
**Recorded on:** a local instance of the real application (commit below), driven by a
scripted browser. Nothing was faked, sped up, or staged off-screen.

The hackathon brief recommends a demo of **no more than three minutes** covering
the problem, a demonstration of the core features, the target users and business
model, and how TapeOut is used. This is that demo.

> The narration is a **synthetic voice** (Piper, `en_US-lessac-medium`). It is
> disclosed here so it is never mistaken for a real person. Every word spoken is
> about what the screen actually shows.

---

## What the video shows

| # | Time | Scene | On screen (real UI) | Says |
|---|---|---|---|---|
| 1 | 0:00 | Problem + idea | Title card → TapeJudge landing page | Every judged competition ends with a number you must trust; the rule lives in the organiser's spreadsheet. |
| 2 | 0:11 | Create a competition | `/new` form filled and submitted | An organiser names the competition and sets the judging panel. |
| 3 | 0:28 | Freeze the rule | `/c/<id>/rubric` — criteria, weights, panel; **Save rubric**; **Freeze & publish →** | The rule compiles to a circuit (gate count, netlist hash) and is frozen. The store refuses later changes, not a promise. |
| 4 | 0:45 | On X Layer | `/c/<id>/publish` — **Prepare publication**; the live processor address, the OKB fee, the transistors to burn; end card | The frozen rule is a TapeOut circuit on X Layer (chain 196). The app reads the live processor and prepares the *unsigned* call. Nothing is sent. |
| 5 | 1:04 | Judge an entry | `/c/<id>/judge` as `judge.carol`; commit the one missing score | Scores are integer, checked against the frozen rule, and committed; a submitted score cannot change. |
| 6 | 1:21 | Compute the ranking | `/c/<id>/results` — Ranking recomputed, **Finalise** | The ranking is computed, not stored; the circuit re-runs over the committed scores every read. |
| 7 | 1:34 | Verify | `/c/<id>/verify` — "The frozen rule is intact"; the local re-run table | Anyone recompiles the rule, re-packs the scores, and re-runs the same evaluator, and can reproduce the result. |
| 8 | 1:51 | Who it is for | Card: target users and the business model | TapeJudge is for anyone whose ranking can be disputed; verification is free forever, and a paid organiser tier is the planned revenue path. |
| 9 | 2:06 | Close | Title card over the verify page | Frozen rules, recorded scores, results anyone can reproduce. "A rule a stranger can re-run." |

Scene frames are kept as evidence in
[`evidence/final-demo-scenes/`](../evidence/final-demo-scenes/) (14 stills plus a
contact sheet), so each claim above can be checked against a frame.

---

## Narration (verbatim)

1. **Problem/idea** — "Every judged competition ends the same way: a number you
   have to trust. If you lose, you cannot check the arithmetic, because the rule
   lives in the organiser's spreadsheet."
2. **Create** — "TapeJudge turns the scoring rule into a circuit. An organiser
   names the competition and sets the judging panel. The rule is configured
   here, and then frozen."
3. **Freeze** — "Freezing compiles the rule into a circuit and fixes it. You can
   see the gate count and the netlist hash. After this point the rule cannot be
   replaced. The store refuses it, not a promise."
4. **On X Layer** — "The frozen rule is a TapeOut circuit on X Layer, chain one
   ninety six, against a live processor. The app reads the processor and prepares
   the unsigned tapeout call: the OKB fee and the transistors to burn. It never
   sends a transaction, and never labels a local circuit as on-chain."
5. **Judge** — "Judges score every entry, criterion by criterion. Each committed
   score is checked against the frozen rule and bound to the judge's account. A
   submitted score cannot be edited."
6. **Ranking** — "The ranking is computed, not stored. TapeJudge re-runs the
   circuit over the committed scores every time it is read, so there is no total
   to trust."
7. **Verify** — "Anyone opens Verify. TapeJudge recompiles the rule, re-packs the
   scores, and re-runs the same evaluator, and shows the exact call to reproduce
   it. A disagreement becomes a fact you can check."
8. **Who it is for** — "TapeJudge is for anyone whose ranking can be disputed:
   hackathons and grant rounds, design and art jams, academic prize committees.
   Verification is free forever. A planned paid organiser tier would add private
   rounds, branded results and API access."
9. **Close** — "Frozen rules. Recorded scores. Results anyone can reproduce.
   TapeJudge: a rule a stranger can re-run."

---

## Honesty notes

- **The data is SYNTHETIC.** Every competition, judge, entry and score shown is
  labelled `SYNTHETIC` in the app and was written by a seeding script. No real
  user, judge or customer appears.
- **Production was not touched.** The recording ran against an isolated local
  SQLite file. `https://tapejudge.vercel.app` and its competition store were not
  used or modified.
- **No transaction is sent.** The publication scene reaches the honest
  `onchain_pending_signature` state: the app prepares and reads; the signature is
  the organiser's. The video never claims a transaction was broadcast.
- **No wallet, no key.** Nothing in the recording connects a wallet or asks for a
  key. Gas and fees are never claimed as paid.
- **The chain read is real.** The processor address, the OKB fee and the
  transistor count shown on the publish page were read live from X Layer
  (`rpc.xlayer.tech`, chain 196) when the page rendered.

---

## How it was produced

Headful Chromium on a virtual display (Xvfb), driven by Playwright, screen-captured
with `ffmpeg` `x11grab`; the browser's own toolbar is cropped out. Each scene is a
separate clip paced to its narration length so audio and video stay aligned, then
concatenated and re-encoded so the segments share one monotonic timeline (a stream
copy carries each segment's own clock and yields non-monotonic-DTS warnings at
every boundary). The narration is synthesised with Piper. The full tool is
[`scripts/demo_video.py`](../scripts/demo_video.py); it is a development-only tool
(it needs Playwright and Piper, which are not project dependencies) and is not part
of the deployed application.

`verify` fails if the file does not decode cleanly, if any scene's frame is
near-blank, or if any scene's audio is silent.

```bash
# Rebuild the isolated dataset, record, and assemble (development machine only):
python scripts/demo_video.py seed
python scripts/demo_video.py narrate
python scripts/demo_video.py record     # needs: a running app + Xvfb :99
python scripts/demo_video.py assemble
python scripts/demo_video.py verify
```

Baseline: `master` @ `5d62342`. The video shows the real routes and the real
service; a later commit changes none of the behaviour it demonstrates.
