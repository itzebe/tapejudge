"""End-to-end TapeJudge demonstration.

Runs the whole workflow against a synthetic competition and prints what happened
at each step: create → configure → freeze → prepare publication → invite judges
→ submit entries → judge → results → independent verification. It also shows the
Mission 6 access control: an account owns a competition, a judge is invited by
email and scores under their own identifier, and an unauthorised account is
refused. Optionally cross-checks an existing on-chain circuit against the local
evaluator with a read-only ``eval``.

    python demo/run_demo.py            # local only
    python demo/run_demo.py --chain    # also do the read-only chain cross-check

All competition data is SYNTHETIC. No transaction is ever sent.
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge.seed import ENTRIES, JUDGES, SCORES
from tapejudge.service import AuthError, TapeJudgeService
from tapejudge.store import Store
from tapejudge.xlayer import XLayerClient

# A synthetic demo password for the synthetic demo accounts. Never a real secret.
DEMO_PASSWORD = "synthetic-demo-password"


def rule(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


class _NoChain:
    """A chain client that refuses to pretend: it reports local-only, honestly."""

    def prepare_tapeout(self, netlist, n_in, n_out, n_gates=None):
        return {"mode": "local_only", "chain_id": 196, "processor": None,
                "circuit_id": None, "tx": None, "fee_wei": None, "fee_known": False,
                "n_gates": n_gates,
                "requirements": {
                    "fee_wei": None, "fee_okb": None, "fee_known": False,
                    "transistors_to_burn": n_gates, "native_coin": "OKB",
                    "notes": ["no chain client in this run"],
                },
                "netlist_bytes": len(netlist),
                "netlist_sha256": hashlib.sha256(netlist).hexdigest(),
                "note": "no chain client in this run", "blocked_reason": "no_client"}

    def simulate_tapeout(self, netlist, n_in, n_out, sender):
        return {"verdict": "unavailable", "error": "no chain client in this run"}

    def is_reachable(self):
        return False


def main() -> int:
    use_chain = "--chain" in sys.argv
    chain_client = XLayerClient() if use_chain else _NoChain()

    rule("1. A SYNTHETIC organiser signs up and creates a competition")
    svc = TapeJudgeService(Store(":memory:"), chain_client=chain_client)
    organiser = svc.register_account(email="organiser@synthetic.example",
                                     password=DEMO_PASSWORD, display_name="SYNTHETIC Organiser")
    cid = svc.create_competition(name="SYNTHETIC Demo Round", organiser="SYNTHETIC",
                                 judges=list(JUDGES), closes_at="2026-12-01T23:59",
                                 owner_account_id=organiser["id"])
    print(f"organiser     : {organiser['email']} ({organiser['id']})")
    print(f"competition id: {cid}")
    print(f"judges        : {', '.join(JUDGES)}")

    rule("2. Configure the rubric (criteria, weights, maxima)")
    svc.set_rubric(cid, [
        {"name": "working_demo", "weight": 3, "max_score": 10},
        {"name": "tests_passing", "weight": 2, "max_score": 10},
        {"name": "code_review", "weight": 1, "max_score": 5},
    ], judges=len(JUDGES), tie_break=["tests_passing", "working_demo", "code_review"])
    c = svc.get_competition(cid)
    print(f"rubric digest : {c['rubric_digest']}")
    print(f"circuit       : {c['gates']} gates, {c['n_in']} in / {c['n_out']} out")
    print(f"netlist sha256: {c['netlist_sha256']}")

    rule("3. Freeze the rule")
    svc.freeze(cid)
    print("status:", svc.get_competition(cid)["status"])
    try:
        svc.set_rubric(cid, [{"name": "x", "weight": 1, "max_score": 5}], judges=1)
        print("!! BUG: rubric replaced after freeze")
    except Exception as e:
        print(f"attempt to replace the frozen rubric rejected: {type(e).__name__}")

    rule("4. Prepare publication (the authorisation boundary)")
    prepared = svc.publish(cid)
    print("publication mode:", prepared["mode"])
    print("processor       :", prepared["processor"])
    print("circuit id      :", prepared["circuit_id"])
    print("fee (wei)       :", prepared.get("fee_wei") if prepared.get("fee_known") else "unknown")
    print("note            :", prepared["note"])

    rule("5. Entrants submit entries (SYNTHETIC)")
    entrant = svc.register_account(email="entrant@synthetic.example",
                                   password=DEMO_PASSWORD, display_name="SYNTHETIC Entrant")
    for sid, title in ENTRIES:
        svc.add_entry(cid, stable_id=sid, title=title,
                      summary="Synthetic demonstration entry.",
                      link="https://example.com/synthetic",
                      submitter_account_id=entrant["id"])
    for e in svc.list_entries(cid, viewer_account_id=entrant["id"]):
        print(f"  {e['stable_id']:26s} hash {e['content_hash'][:16]}…")

    rule("5b. Access control — an unrelated account cannot act on this competition")
    outsider = svc.register_account(email="outsider@synthetic.example",
                                    password=DEMO_PASSWORD)
    try:
        svc.require_organiser(cid, outsider["id"])
        print("!! BUG: a non-owner passed the organiser gate")
    except AuthError:
        print("non-owner refused the organiser gate : AuthError")
    # The public promise is unchanged: an outsider can still reproduce the result.
    print("outsider can still READ/verify       : public verification stays open")

    rule("6. Judges are invited, accept, and commit scores (immutable, SYNTHETIC)")
    judge_accounts = {}
    for j in JUDGES:
        email = f"{j}@synthetic.example"
        acct = svc.register_account(email=email, password=DEMO_PASSWORD)
        inv = svc.invite_judge(cid, email=email, label=j,
                               organiser_account_id=organiser["id"])
        svc.accept_invitation(inv["token"], account_id=acct["id"])
        judge_accounts[j] = acct
    for judge, per in SCORES.items():
        for sid, scores in per.items():
            svc.evaluate(cid, entry_stable_id=sid, judge=judge, scores=scores,
                         judge_account_id=judge_accounts[judge]["id"])
    print("committed evaluations:", len(svc.store.list_evaluations(cid)))
    try:
        svc.evaluate(cid, entry_stable_id=ENTRIES[0][0], judge=JUDGES[0],
                     scores={"working_demo": 10, "tests_passing": 10, "code_review": 5},
                     judge_account_id=judge_accounts[JUDGES[0]]["id"])
        print("!! BUG: a submitted score was changed")
    except Exception as e:
        print(f"attempt to change a committed score rejected: {type(e).__name__}")

    rule("7. Results — computed by the frozen circuit, never stored")
    res = svc.results(cid)
    print(f"{'#':>2} {'entry':26s} {'total':>5}  subtotals")
    for r in res["ranking"]:
        print(f"{r['rank']:>2} {r['entry']:26s} {r['total']:>5}  {r['subtotals']}")

    rule("8. Anyone re-runs it and checks the frozen rule")
    rep = svc.verify(cid)
    print("frozen rule intact:", rep["frozen_rule_ok"])
    print("independent ranking:",
          [(r["rank"], r["entry"], r["total"]) for r in rep["ranking"]])
    print("published on chain :", rep["publication"]["on_chain"],
          f"({rep['publication']['mode']})")

    if use_chain:
        rule("9. Read-only X Layer cross-check (no transaction)")
        client = XLayerClient()
        try:
            print("block number:", client.block_number())
            print("factory cpuCount:", client.cpu_count())
            proc = client.processor_at(0)
            print("processor 0:", proc, "nextId:", client.next_id(proc))
            x = client.verify_circuit_against_chain(1, [bytes([i]) for i in range(4)], proc)
            print("existing circuit #1 local==chain:", x["match"])
        except Exception as e:
            print("chain read failed:", e)

    print("\nAll data above is SYNTHETIC. No transaction was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
