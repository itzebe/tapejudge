"""Seed the TapeJudge database with a SYNTHETIC demonstration competition.

Everything this script writes is invented for demonstration: the competition,
the judges, the entries and the scores are all fabricated. They are labelled
``SYNTHETIC`` wherever they appear so they can never be mistaken for real users,
real judges or a real result. Nothing here touches a chain, a wallet or a
network.

Usage:
    python -m tapejudge.seed [db_path]

If ``db_path`` is omitted the default ``:memory:`` store is used, which is only
useful for an in-process demo. Pass a file path to seed a running server.
"""
from __future__ import annotations

import sys

from .service import TapeJudgeService
from .store import Store

SYNTHETIC_NAME = "SYNTHETIC — Demo Judging Round"

# Fabricated judge identifiers. They are not real people.
JUDGES = ["judge.synthetic.alice", "judge.synthetic.bob", "judge.synthetic.carol"]

# Fabricated entries and scores. The scores are invented, not measured.
CRITERIA = [
    {"name": "working_demo", "weight": 3, "max_score": 10},
    {"name": "tests_passing", "weight": 2, "max_score": 10},
    {"name": "code_review", "weight": 1, "max_score": 5},
]
TIE_BREAK = ["tests_passing", "working_demo", "code_review"]
ENTRIES = [
    ("team-synthetic-aurora", "Aurora (synthetic)"),
    ("team-synthetic-borealis", "Borealis (synthetic)"),
    ("team-synthetic-cirrus", "Cirrus (synthetic)"),
]
SCORES = {
    "judge.synthetic.alice": {
        "team-synthetic-aurora": {"working_demo": 9, "tests_passing": 8, "code_review": 4},
        "team-synthetic-borealis": {"working_demo": 6, "tests_passing": 7, "code_review": 3},
        "team-synthetic-cirrus": {"working_demo": 8, "tests_passing": 5, "code_review": 5},
    },
    "judge.synthetic.bob": {
        "team-synthetic-aurora": {"working_demo": 8, "tests_passing": 9, "code_review": 5},
        "team-synthetic-borealis": {"working_demo": 7, "tests_passing": 6, "code_review": 4},
        "team-synthetic-cirrus": {"working_demo": 7, "tests_passing": 6, "code_review": 3},
    },
    "judge.synthetic.carol": {
        "team-synthetic-aurora": {"working_demo": 9, "tests_passing": 7, "code_review": 4},
        "team-synthetic-borealis": {"working_demo": 5, "tests_passing": 8, "code_review": 2},
        "team-synthetic-cirrus": {"working_demo": 9, "tests_passing": 4, "code_review": 4},
    },
}


def seed(db_path: str = ":memory:", *, chain_client=None) -> dict:
    store = Store(db_path)
    svc = TapeJudgeService(store, chain_client=chain_client)
    cid = svc.create_competition(
        name=SYNTHETIC_NAME,
        description=("A synthetic demonstration round. Every judge, entry and "
                     "score here is fabricated to show the workflow; none of it "
                     "is real data."),
        organiser="SYNTHETIC organiser",
        opens_at="2026-11-01T00:00",
        closes_at="2026-12-01T23:59",
        judges=JUDGES,
        prize_note="SYNTHETIC 1,000 USDT (illustrative only)")
    svc.set_rubric(cid, CRITERIA, judges=len(JUDGES), tie_break=TIE_BREAK)
    svc.freeze(cid)
    for sid, title in ENTRIES:
        svc.add_entry(cid, stable_id=sid, title=title,
                      summary="Synthetic entry for demonstration.",
                      link="https://example.com/synthetic")
    for judge, per_entry in SCORES.items():
        for sid, scores in per_entry.items():
            svc.evaluate(cid, entry_stable_id=sid, judge=judge, scores=scores)
    return {"competition_id": cid, "results": svc.results(cid)}


DEMO_ACCOUNTS = {
    "organiser": ("organiser@demo.tapejudge.test", "Demo Organiser"),
    "judge": ("judge@demo.tapejudge.test", "Demo Judge"),
    "entrant": ("entrant@demo.tapejudge.test", "Demo Entrant"),
}
DEMO_PASSWORD = "synthetic-demo-password"  # noqa: S105 — a demo fixture, not a secret


def seed_journey(db_path: str = ":memory:", *, chain_client=None) -> dict:
    """Seed a full account-backed demonstration and return its identifiers.

    Creates three accounts (organiser, judge, entrant), a competition owned by
    the organiser, a frozen rubric, entries submitted by the entrant, an accepted
    judge invitation, and a committed score. Every value is synthetic.
    """
    store = Store(db_path)
    svc = TapeJudgeService(store, chain_client=chain_client)
    accounts = {}
    for key, (email, name) in DEMO_ACCOUNTS.items():
        accounts[key] = svc.register_account(
            email=email, password=DEMO_PASSWORD, display_name=name)
    org = accounts["organiser"]["id"]
    cid = svc.create_competition(
        name="SYNTHETIC — Demo Judging Round",
        description=("A synthetic, account-backed demonstration round. Every "
                     "account, entry and score is fabricated to show the workflow."),
        organiser="Demo Organiser",
        opens_at="2026-11-01T00:00", closes_at="2026-12-01T23:59",
        judges=["demo.judge"], prize_note="SYNTHETIC 1,000 USDT (illustrative only)",
        owner_account_id=org)
    svc.set_rubric(cid, CRITERIA, judges=1, tie_break=TIE_BREAK)
    svc.freeze(cid)
    for sid, title in ENTRIES:
        svc.add_entry(cid, stable_id=sid, title=title,
                      summary="Synthetic entry for demonstration.",
                      link="https://example.com/synthetic",
                      submitter_account_id=accounts["entrant"]["id"])
    invite = svc.invite_judge(cid, email=DEMO_ACCOUNTS["judge"][0], label="demo.judge",
                              organiser_account_id=org)
    svc.accept_invitation(invite["token"], account_id=accounts["judge"]["id"])
    for sid in (ENTRIES[0][0], ENTRIES[1][0], ENTRIES[2][0]):
        svc.evaluate(cid, entry_stable_id=sid, judge="demo.judge",
                     scores=SCORES["judge.synthetic.alice"][sid],
                     judge_account_id=accounts["judge"]["id"])
    return {"competition_id": cid, "accounts": DEMO_ACCOUNTS,
            "password": DEMO_PASSWORD, "results": svc.results(cid)}


if __name__ == "__main__":
    args = sys.argv[1:]
    journey = "--journey" in args
    args = [a for a in args if a != "--journey"]
    path = args[0] if args else ":memory:"
    if journey:
        out = seed_journey(path)
        print(f"Seeded SYNTHETIC account-backed demo {out['competition_id']} into {path}")
        print(f"  sign-in accounts (password: {out['password']}):")
        for role, (email, _name) in out["accounts"].items():
            print(f"    {role:10s} {email}")
        for row in out["results"]["ranking"]:
            print(f"  #{row['rank']} {row['entry']:26s} total={row['total']:3d}")
    else:
        out = seed(path)
        print(f"Seeded SYNTHETIC competition {out['competition_id']} into {path}")
        for row in out["results"]["ranking"]:
            print(f"  #{row['rank']} {row['entry']:26s} total={row['total']:3d} {row['subtotals']}")

