#!/usr/bin/env python3
"""Produce the TapeJudge demo video from the REAL application.

Development-only tool. It is not imported by the app, and its dependencies
(Playwright, Piper, ffmpeg, Xvfb) are NOT project dependencies. Nothing here
touches production, a wallet, or a chain; every value it writes is SYNTHETIC.

    python scripts/demo_video.py seed      # build the isolated demo database
    python scripts/demo_video.py narrate   # synthesise the narration (Piper)
    python scripts/demo_video.py record    # drive the app and capture the screen
    python scripts/demo_video.py assemble  # crop, mux and concatenate
    python scripts/demo_video.py verify    # frame/audio checks on the result
    python scripts/demo_video.py all       # seed + narrate + assemble (no record)

`record` expects a running app on $PORT (default 12020) and Xvfb on $DISPLAY
(default :99). See docs/FINAL_DEMO_VIDEO.md.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("TAPEJUDGE_PORT", "12020"))
BASE = f"http://127.0.0.1:{PORT}"
DISPLAY = os.environ.get("DISPLAY", ":99")
WORK = Path(os.environ.get("TAPEJUDGE_DEMO_WORK",
                           Path(tempfile.gettempdir()) / "tapejudge-demo"))
DB = WORK / "demo.db"
CLIPS, NAR, BUILD = WORK / "clips", WORK / "nar", WORK / "build"
EVID = REPO / "evidence" / "final-demo-scenes"
OUT = REPO / "demo" / "tapejudge-final-demo.mp4"

W, H, FPS = 1280, 800, 30
CW, CH, CY = 1280, 712, 88                 # cropped page size + top offset
CROP = f"crop={CW}:{CH}:0:{CY}"
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
TARGETS = {"intro": 11, "create": 17, "freeze": 17, "xlayer": 19,
           "evaluate": 17, "calculate": 13, "verify": 17, "who": 15, "outro": 9}

PASSWORD = "synthetic-demo-password"
ORG = "organiser@demo.tapejudge.test"
CAROL = "judge.carol@demo.tapejudge.test"

CRITERIA = [
    {"name": "working_demo", "weight": 3, "max_score": 10},
    {"name": "tests_passing", "weight": 2, "max_score": 10},
    {"name": "code_review", "weight": 1, "max_score": 5},
]
TIE_BREAK = ["tests_passing", "working_demo", "code_review"]
PANEL = ["judge.alice", "judge.bob", "judge.carol"]
ENTRIES = [
    ("aurora", "Aurora — streaming settlement prototype"),
    ("borealis", "Borealis — batch payout scheduler"),
    ("cirrus", "Cirrus — on-chain invoicing demo"),
]
ALICE = {"aurora": {"working_demo": 9, "tests_passing": 8, "code_review": 4},
         "borealis": {"working_demo": 6, "tests_passing": 7, "code_review": 3},
         "cirrus": {"working_demo": 8, "tests_passing": 5, "code_review": 5}}
BOB = {"aurora": {"working_demo": 8, "tests_passing": 9, "code_review": 5},
       "borealis": {"working_demo": 7, "tests_passing": 6, "code_review": 4},
       "cirrus": {"working_demo": 7, "tests_passing": 6, "code_review": 3}}
CAROL_SEEDED = {"aurora": {"working_demo": 9, "tests_passing": 7, "code_review": 4},
                "cirrus": {"working_demo": 9, "tests_passing": 4, "code_review": 4}}
ACCOUNTS = {
    "organiser": "organiser@demo.tapejudge.test",
    "entrant": "entrant@demo.tapejudge.test",
    "alice": "judge.alice@demo.tapejudge.test",
    "bob": "judge.bob@demo.tapejudge.test",
    "carol": "judge.carol@demo.tapejudge.test",
}

NARRATION = [
    ("intro",
     "Every judged competition ends the same way: a number you have to trust. "
     "If you lose, you cannot check the arithmetic, because the rule lives in the "
     "organiser's spreadsheet."),
    ("create",
     "TapeJudge turns the scoring rule into a circuit. An organiser names the "
     "competition and sets the judging panel. The rule is configured here, and "
     "then frozen."),
    ("freeze",
     "Freezing compiles the rule into a circuit and fixes it. You can see the "
     "gate count and the netlist hash. After this point the rule cannot be "
     "replaced. The store refuses it, not a promise."),
    ("evaluate",
     "Judges score every entry, criterion by criterion. Each committed score is "
     "checked against the frozen rule and bound to the judge's account. A "
     "submitted score cannot be edited."),
    ("calculate",
     "The ranking is computed, not stored. TapeJudge re-runs the circuit over the "
     "committed scores every time it is read, so there is no total to trust."),
    ("verify",
     "Anyone opens Verify. TapeJudge recompiles the rule, re-packs the scores, "
     "and re-runs the same evaluator, and shows the exact call to reproduce it. "
     "A disagreement becomes a fact you can check."),
    ("xlayer",
     "The frozen rule is a TapeOut circuit on X Layer, chain one ninety six, "
     "against a live processor. The app reads the processor and prepares the "
     "unsigned tapeout call: the OKB fee and the transistors to burn. It never "
     "sends a transaction, and never labels a local circuit as on-chain."),
    ("who",
     "TapeJudge is for anyone whose ranking can be disputed: hackathons and grant "
     "rounds, design and art jams, academic prize committees. Verification is free "
     "forever. A planned paid organiser tier would add private rounds, branded "
     "results and API access."),
    ("outro",
     "Frozen rules. Recorded scores. Results anyone can reproduce. TapeJudge: a "
     "rule a stranger can re-run."),
]


def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- seed ------- #
def seed():
    """Build the isolated synthetic dataset by running the real service."""
    sys.path.insert(0, str(REPO))
    from tapejudge.service import TapeJudgeService
    from tapejudge.store import make_store

    WORK.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    store = make_store(str(DB))
    svc = TapeJudgeService(store)

    ids = {k: svc.register_account(email=e, password=PASSWORD,
                                   display_name=f"SYNTHETIC {k.title()}")["id"]
           for k, e in ACCOUNTS.items()}
    cid = svc.create_competition(
        name="SYNTHETIC — X Layer Build Jam",
        description=("A synthetic demonstration round for the TapeJudge demo "
                     "video. Every account, entry and score here is invented; none "
                     "of it is real data."),
        organiser="SYNTHETIC Organiser",
        opens_at="2026-11-01T00:00", closes_at="2026-12-01T23:59",
        judges=PANEL, prize_note="SYNTHETIC 10,000 USDT (illustrative only)",
        owner_account_id=ids["organiser"])
    svc.set_rubric(cid, CRITERIA, judges=len(PANEL), tie_break=TIE_BREAK)
    svc.freeze(cid)
    for sid, title in ENTRIES:
        svc.add_entry(cid, stable_id=sid, title=title,
                      summary="SYNTHETIC demo entry.",
                      link="https://example.com/synthetic",
                      submitter_account_id=ids["entrant"])
    for key, label in (("alice", "judge.alice"), ("bob", "judge.bob"),
                       ("carol", "judge.carol")):
        inv = svc.invite_judge(cid, email=ACCOUNTS[key], label=label,
                               organiser_account_id=ids["organiser"])
        svc.accept_invitation(inv["token"], account_id=ids[key])

    def commit(scores, label, acct):
        for sid, s in scores.items():
            svc.evaluate(cid, entry_stable_id=sid, judge=label, scores=s,
                         judge_account_id=ids[acct])

    commit(ALICE, "judge.alice", "alice")
    commit(BOB, "judge.bob", "bob")
    commit(CAROL_SEEDED, "judge.carol", "carol")   # carol still owes borealis

    (WORK / "cids.json").write_text(json.dumps({"judged_cid": cid}))
    res = svc.results(cid)
    print(f"seeded {cid} into {DB}")
    for row in res["ranking"]:
        print(f"  #{row['rank']} {row['entry']:10s} total={row['total']:3d}")
    print("incomplete:", res.get("incomplete"))


# ------------------------------------------------------------- narrate ------- #
def narrate():
    """Synthesise the narration with Piper (a genuine TTS engine)."""
    from piper import PiperVoice

    voice_path = os.environ.get("TAPEJUDGE_PIPER_VOICE",
                                "/tmp/piper-voices/en_US-lessac-medium.onnx")
    NAR.mkdir(parents=True, exist_ok=True)
    voice = PiperVoice.load(voice_path, config_path=voice_path + ".json")
    durations = {}
    for name, text in NARRATION:
        path = NAR / f"{name}.wav"
        with wave.open(str(path), "wb") as wf:
            voice.synthesize_wav(text, wf)
        with wave.open(str(path)) as wf:
            durations[name] = round(wf.getnframes() / wf.getframerate(), 2)
        print(f"{name:10s} {durations[name]:5.2f}s")
    (NAR / "durations.json").write_text(json.dumps(durations, indent=2))
    for name, target in TARGETS.items():
        assert target >= durations[name], f"{name}: target {target} < narration {durations[name]}"


# -------------------------------------------------------------- record ------- #
def record():
    """Drive the real app in a headful browser and capture the screen."""
    os.environ["DISPLAY"] = DISPLAY
    from playwright.sync_api import sync_playwright

    judged_cid = json.loads((WORK / "cids.json").read_text())["judged_cid"]
    CLIPS.mkdir(parents=True, exist_ok=True)
    EVID.mkdir(parents=True, exist_ok=True)

    class Rec:
        def __init__(self, page, name):
            self.page, self.name, self.t0, self.proc = page, name, None, None

        def start(self):
            self.t0 = time.time()
            self.proc = subprocess.Popen(
                ["ffmpeg", "-y", "-f", "x11grab", "-framerate", str(FPS),
                 "-video_size", f"{W}x{H}", "-i", f"{DISPLAY}.0+0,0", *ENC,
                 str(CLIPS / f"{self.name}.mp4")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def fill_to(self, target):
            time.sleep(max(0.0, target - (time.time() - self.t0)))

        def stop(self):
            time.sleep(0.3)
            self.proc.send_signal(2)
            self.proc.wait(timeout=30)

    def settle(page, ms=450):
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(ms)

    def login(page, email):
        page.goto(BASE + "/login")
        settle(page)
        page.fill("#email", email)
        page.fill("#password", PASSWORD)
        page.click("#main form button.primary")
        settle(page)

    def logout(page):
        page.click('form[action*="logout"] button')
        settle(page)

    def overlay(page, kicker, title, sub=""):
        page.evaluate(
            """([k,t,s]) => {
              const d = document.createElement('div'); d.id = 'demo-card';
              const K = 'font:600 .8rem/1 ui-sans-serif;letter-spacing:.28em;'
                + 'text-transform:uppercase;color:#dfb79c;margin:0 0 22px';
              const T = 'font:600 3.0rem/1.12 Georgia,serif;margin:0;max-width:20ch';
              const S = 'font:400 1.15rem/1.5 ui-sans-serif;color:#c7b99f;'
                + 'margin:26px 0 0;max-width:44ch';
              d.style.cssText = 'position:fixed;inset:0;z-index:99999;background:#17130e;'
                + 'color:#f4efe4;display:flex;flex-direction:column;justify-content:center;'
                + 'align-items:center;text-align:center;padding:0 12%;';
              d.innerHTML = '<p style="' + K + '">' + k + '</p>'
                + '<h1 style="' + T + '">' + t + '</h1>'
                + (s ? '<p style="' + S + '">' + s + '</p>' : '');
              document.body.appendChild(d);
            }""", [kicker, title, sub])

    def clear_overlay(page):
        page.evaluate("() => document.getElementById('demo-card')?.remove()")

    def mark(page, name):
        page.screenshot(path=str(EVID / f"{name}.png"))

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("TAPEJUDGE_CHROMIUM", "/usr/bin/chromium"),
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--window-position=0,0", f"--window-size={W},{H}",
                  "--app=" + BASE + "/", "--hide-scrollbars",
                  "--force-device-scale-factor=1"])
        page = browser.new_page(viewport={"width": W, "height": 720})
        page.goto(BASE + "/")
        settle(page)
        run_info = {}

        rec = Rec(page, "intro")
        rec.start()
        overlay(page, "X Layer TapeOut Accelerate",
                "Competition results should survive scrutiny.",
                "Freeze the rules. Record the scores. Let anyone check the result.")
        time.sleep(5.4)
        clear_overlay(page)
        page.goto(BASE + "/")
        settle(page)
        page.wait_for_timeout(900)
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(1600)
        rec.fill_to(TARGETS["intro"])
        rec.stop()

        rec = Rec(page, "create")
        rec.start()
        page.goto(BASE + "/")
        settle(page)
        page.wait_for_timeout(1500)
        mark(page, "01-landing")
        login(page, ORG)
        page.goto(BASE + "/new")
        settle(page)
        page.fill("#name", "SYNTHETIC — X Layer Build Jam")
        page.fill("#description", "A synthetic round recorded for the TapeJudge demo.")
        page.fill("#organiser", "SYNTHETIC Organiser")
        page.fill("#prize_note", "SYNTHETIC 10,000 USDT (illustrative only)")
        page.fill("#judges_raw", "judge.alice\njudge.bob\njudge.carol")
        page.wait_for_timeout(1200)
        mark(page, "02-new-competition")
        page.click("#main form button.primary")
        settle(page)
        run_info["new_cid"] = page.url.split("/c/")[1].split("/")[0]
        rec.fill_to(TARGETS["create"])
        rec.stop()

        rec = Rec(page, "freeze")
        rec.start()
        page.fill("#judges", "3")
        for i in range(3):
            page.locator("input[name=crit_weight]").nth(i).fill(["3", "2", "1"][i])
        page.wait_for_timeout(700)
        mark(page, "03-rubric-configured")
        page.click("#rubric-form button[type=submit]")
        settle(page)
        page.wait_for_timeout(600)
        page.locator("select#tie_break").select_option(TIE_BREAK)
        page.wait_for_timeout(500)
        page.click("#rubric-form button[type=submit]")
        settle(page)
        page.wait_for_timeout(600)
        page.get_by_role("heading", name="Freeze the rule").scroll_into_view_if_needed()
        page.wait_for_timeout(700)
        mark(page, "04-freeze-panel")
        page.click('button[name=action][value=freeze]')
        settle(page)
        page.wait_for_timeout(1000)
        mark(page, "05-frozen")
        rec.fill_to(TARGETS["freeze"])
        rec.stop()

        rec = Rec(page, "xlayer")
        rec.start()
        page.get_by_role("heading", name="Publication state").scroll_into_view_if_needed()
        page.wait_for_timeout(1400)
        mark(page, "06-publication-local")
        page.click('button[name=action][value=publish]')
        settle(page)
        page.wait_for_timeout(1600)
        mark(page, "07-publication-awaiting-signature")
        page.get_by_text("Transistors to burn").scroll_into_view_if_needed()
        page.wait_for_timeout(1800)
        rec.fill_to(TARGETS["xlayer"])
        rec.stop()

        rec = Rec(page, "evaluate")
        rec.start()
        logout(page)
        login(page, CAROL)
        page.goto(BASE + f"/c/{judged_cid}/judge")
        settle(page)
        page.get_by_role("heading", name="Judge evaluation").scroll_into_view_if_needed()
        page.wait_for_timeout(900)
        mark(page, "08-judge-page")
        page.fill("#s-borealis-working_demo", "5")
        page.fill("#s-borealis-tests_passing", "8")
        page.fill("#s-borealis-code_review", "2")
        page.locator("#j-borealis").fill("Synthetic justification for the demo.")
        page.wait_for_timeout(900)
        mark(page, "09-score-entry")
        page.locator("form:has(#s-borealis-working_demo) button[type=submit]").click()
        settle(page)
        page.wait_for_timeout(1200)
        mark(page, "10-score-committed")
        rec.fill_to(TARGETS["evaluate"])
        rec.stop()

        rec = Rec(page, "calculate")
        rec.start()
        logout(page)
        login(page, ORG)
        page.goto(BASE + f"/c/{judged_cid}/results")
        settle(page)
        page.get_by_role("heading", name="Ranking").scroll_into_view_if_needed()
        page.wait_for_timeout(1600)
        mark(page, "11-ranking")
        page.get_by_role("link", name="Reproduce this ranking").scroll_into_view_if_needed()
        page.wait_for_timeout(900)
        fin = page.locator('form[action*="results"] button[type=submit]')
        if fin.count() > 0:
            fin.first.click()
            settle(page)
            page.wait_for_timeout(1200)
            mark(page, "12-finalised")
        rec.fill_to(TARGETS["calculate"])
        rec.stop()

        rec = Rec(page, "verify")
        rec.start()
        page.goto(BASE + f"/c/{judged_cid}/verify")
        settle(page)
        page.get_by_role("heading", name="The frozen rule is intact").scroll_into_view_if_needed()
        page.wait_for_timeout(1500)
        mark(page, "13-verify-intact")
        page.get_by_role("heading", name="Re-run locally in this page").scroll_into_view_if_needed()
        page.wait_for_timeout(2200)
        mark(page, "14-verify-rerun")
        rec.fill_to(TARGETS["verify"])
        rec.stop()

        rec = Rec(page, "outro")
        rec.start()
        page.goto(BASE + f"/c/{judged_cid}/verify")
        settle(page)
        overlay(page, "TapeJudge",
                "The rules are frozen. The scores are recorded. The results can be checked.",
                "A rule a stranger can re-run — on X Layer.")
        rec.fill_to(TARGETS["outro"])
        rec.stop()

        browser.close()
    (WORK / "run.json").write_text(json.dumps(run_info, indent=2))
    print("recorded clips into", CLIPS)


# ------------------------------------------------------------- assemble ------ #
def _title_card():
    """Draw the X Layer card from the processor values the app read live."""
    from PIL import Image, ImageDraw, ImageFont

    cid = json.loads((WORK / "run.json").read_text())["new_cid"]
    comp = json.loads(urllib.request.urlopen(
        f"{BASE}/api/competitions/{cid}", timeout=8).read())
    pub = comp["publication"]
    proc, fee = pub.get("processor"), pub.get("fee_okb")

    img = Image.new("RGB", (CW, CH), (23, 19, 14))
    d = ImageDraw.Draw(img)

    def font(size, bold=False):
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        try:
            return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)
        except OSError:
            return ImageFont.load_default()

    def center(text, y, f, fill):
        d.text(((CW - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill)

    center("ON X LAYER", 200, font(22, True), "#dfb79c")
    center("TapeOut, on chain 196", 248, font(56, True), "#f4efe4")
    center(f"processor {proc[:22]}…" if proc else "processor unavailable",
           340, font(26), "#c7b99f")
    center(f"tape-out fee {fee:.4f} OKB  ·  transistors to burn "
           f"{pub.get('transistors_to_burn')}" if fee is not None
           else "tape-out fee read live from the processor",
           382, font(26), "#c7b99f")
    center("→ the frozen rule, reproducible on chain", 468, font(28, True), "#1a1510")
    center("Prepared and read live. Nothing is sent by the app.",
           548, font(22), "#8f8676")
    out = WORK / "xlayer-card.png"
    img.save(out)
    return out


def _who_card():
    """A single card for the audience + business-model scene."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (CW, CH), (23, 19, 14))
    d = ImageDraw.Draw(img)

    def font(size, bold=False):
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        try:
            return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)
        except OSError:
            return ImageFont.load_default()

    def eyebrow(text, y):
        d.text((120, y), text, font=font(20, True), fill="#dfb79c")

    eyebrow("WHO IT IS FOR", 96)
    for i, line in enumerate(("Hackathons and grant rounds",
                              "Design and art jams",
                              "Academic prize committees")):
        y = 150 + i * 56
        d.ellipse((120, y + 12, 132, y + 24), fill="#a5431d")
        d.text((152, y), line, font=font(34, True), fill="#f4efe4")

    d.line((120, 372, CW - 120, 372), fill="#3a3126", width=2)
    eyebrow("BUSINESS MODEL", 410)
    d.text((120, 458), "Verification — free, forever",
           font=font(34, True), fill="#f4efe4")
    d.text((120, 512), "Organiser tier — private rounds, branded",
           font=font(30), fill="#c7b99f")
    d.text((120, 552), "results, API access", font=font(30), fill="#c7b99f")

    out = WORK / "who-card.png"
    img.save(out)
    return out


def _crop_clip(name, dur=None):
    out = BUILD / f"{name}_crop.mp4"
    args = ["ffmpeg", "-y", "-i", str(CLIPS / f"{name}.mp4"), "-vf", CROP]
    if dur is not None:
        args += ["-t", str(dur)]
    run(args + ENC + [str(out)])
    return out


def assemble():
    BUILD.mkdir(parents=True, exist_ok=True)
    card = WORK / "xlayer-card.png"
    if (WORK / "run.json").exists():
        card = _title_card()
    card_mp4 = BUILD / "xlayer-card.mp4"
    if card.exists():
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(card), "-t", "4", "-r", "30",
             *ENC, str(card_mp4)])

    who_png = _who_card()
    who_mp4 = BUILD / "who-card.mp4"
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(who_png), "-t", str(TARGETS["who"]),
         "-r", "30", *ENC, str(who_mp4)])

    scenes = []
    for name, T in TARGETS.items():
        aud = BUILD / f"{name}.wav"
        run(["ffmpeg", "-y", "-i", str(NAR / f"{name}.wav"), "-af", "apad",
             "-t", str(T), "-ar", "22050", "-ac", "1", str(aud)])
        if name == "xlayer" and card_mp4.exists():
            lead = _crop_clip("xlayer", dur=T - 4)
            lst = BUILD / "xlayer_src.txt"
            lst.write_text(f"file '{lead.name}'\nfile '{card_mp4.name}'\n")
            vsrc = BUILD / "xlayer_src.mp4"
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                 "-c", "copy", str(vsrc)])
        elif name == "who":
            vsrc = who_mp4
        else:
            vsrc = _crop_clip(name)
        av = BUILD / f"{name}_av.mp4"
        run(["ffmpeg", "-y", "-i", str(vsrc), "-i", str(aud),
             "-map", "0:v:0", "-map", "1:a:0", "-t", str(T),
             "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "22050",
             "-movflags", "+faststart", str(av)])
        scenes.append(av)
        print("built", av.name)

    lst = BUILD / "concat.txt"
    lst.write_text("".join(f"file '{f.name}'\n" for f in scenes))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Re-encode the concatenation (rather than stream-copy) so ffmpeg rewrites
    # the per-segment timestamps into one monotonic timeline; a stream copy
    # carries each segment's own clock and produces non-monotonic DTS warnings
    # at every scene boundary.
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "22050",
         "-movflags", "+faststart", str(OUT)])
    print("wrote", OUT)


# --------------------------------------------------------------- verify ------ #
def verify():
    """Decode, frame-content, blank-frame and per-scene audio checks."""
    import numpy as np
    from PIL import Image

    OUTV = WORK / "verify"
    OUTV.mkdir(exist_ok=True)
    starts, t = {}, 0.0
    for name, T in TARGETS.items():
        starts[name] = t
        t += T

    # A full decode must be clean: no error/warning lines (a non-monotonic DTS at
    # a scene boundary means the concat did not re-time the segments).
    dec = subprocess.run(["ffmpeg", "-v", "warning", "-i", str(OUT), "-f", "null", "-"],
                         capture_output=True, text=True)
    decode_warnings = [ln for ln in dec.stderr.splitlines() if ln.strip()]

    def frame_at(ts, path):
        subprocess.run(["ffmpeg", "-y", "-ss", f"{ts}", "-i", str(OUT),
                        "-frames:v", "1", str(path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def audio_rms(ss, dur):
        r = subprocess.run(["ffmpeg", "-ss", f"{ss}", "-t", f"{dur}", "-i", str(OUT),
                            "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True)
        for line in r.stderr.splitlines():
            if "mean_volume" in line:
                return float(line.split("mean_volume:")[1].split("dB")[0])
        return None

    report, thumbs, problems = {"scenes": {}}, [], []
    for name, T in TARGETS.items():
        mid = starts[name] + T / 2
        f = OUTV / f"{name}.png"
        frame_at(mid, f)
        im = np.asarray(Image.open(f).convert("L"))
        rms = audio_rms(starts[name] + 0.2, T - 0.4)
        report["scenes"][name] = {"mid": round(mid, 2),
                                  "luma_std": round(float(im.std()), 1),
                                  "audio_mean_db": rms}
        if im.std() < 3:
            problems.append(f"{name}: near-blank frame")
        if rms is None or rms < -60:
            problems.append(f"{name}: no audio")
        thumbs.append((name, Image.open(f).convert("RGB").resize((480, 300))))
    cols = 4
    rows = -(-len(thumbs) // cols)
    sheet = Image.new("RGB", (480 * cols, 300 * rows), (23, 19, 14))
    for i, (_n, th) in enumerate(thumbs):
        sheet.paste(th, ((i % cols) * 480, (i // cols) * 300))
    sheet.save(EVID / "contact-sheet.png")
    report["decode"] = {"clean": not decode_warnings, "warnings": decode_warnings}
    if decode_warnings:
        problems.append(f"decode: {len(decode_warnings)} warning line(s)")
    print(json.dumps(report, indent=2))
    print("PROBLEMS:", problems or "none")
    return 1 if problems else 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "seed":
        seed()
    elif cmd == "narrate":
        narrate()
    elif cmd == "record":
        record()
    elif cmd == "assemble":
        assemble()
    elif cmd == "verify":
        sys.exit(verify())
    elif cmd == "all":
        seed()
        narrate()
        assemble()
    else:
        sys.exit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
