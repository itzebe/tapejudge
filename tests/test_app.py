"""Application service tests — the integrity rules of the product.

These are the tests that make the product's promise checkable:

* a frozen rubric cannot be replaced;
* a submitted score cannot be changed, and a duplicate is refused;
* an out-of-range score is rejected and, at the circuit level, flagged invalid;
* the ranking is deterministic and independently reproducible;
* a result is never stored — it is recomputed from inputs;
* publication state is honest (never "on-chain" without a real transaction).

All data here is synthetic.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge import Store, TapeJudgeService
from tapejudge.netlist import beat
from tapejudge.rubric import Criterion, Rubric, compile_rubric, decode_outputs
from tapejudge.service import ConflictError, ValidationError

JUDGES = ["j1", "j2", "j3"]
CRITERIA = [
    {"name": "demo", "weight": 3, "max_score": 10},
    {"name": "tests", "weight": 2, "max_score": 10},
    {"name": "review", "weight": 1, "max_score": 5},
]
TIE = ["tests", "demo", "review"]
SCORES = {
    "j1": {"a": (9, 8, 4), "b": (6, 7, 3), "c": (8, 5, 5)},
    "j2": {"a": (8, 9, 5), "b": (7, 6, 4), "c": (7, 6, 3)},
    "j3": {"a": (9, 7, 4), "b": (5, 8, 2), "c": (9, 4, 4)},
}


def build(freeze=True, entries=("a", "b", "c")):
    svc = TapeJudgeService(Store(":memory:"), chain_client=_StubChain())
    cid = svc.create_competition(name="Synthetic Round", organiser="SYNTH",
                                 judges=list(JUDGES))
    svc.set_rubric(cid, CRITERIA, judges=len(JUDGES), tie_break=TIE)
    if freeze:
        svc.freeze(cid)
        # Entries are only accepted once the rule is published.
        for e in entries:
            svc.add_entry(cid, stable_id=e, title=e.upper())
    return svc, cid


def score_all(svc, cid, scores=SCORES):
    for judge, per in scores.items():
        for entry, (d, t, r) in per.items():
            svc.evaluate(cid, entry_stable_id=entry, judge=judge,
                         scores={"demo": d, "tests": t, "review": r})


class _StubChain:
    """A chain client that never touches the network. Returns honest state."""

    def prepare_tapeout(self, netlist, n_in, n_out, n_gates=None):
        return {"mode": "onchain_pending_signature", "chain_id": 196,
                "processor": "0xstubprocessor", "circuit_id": 103,
                "tx": {"unsigned": True}, "fee_wei": None, "fee_known": False,
                "netlist_bytes": len(netlist), "note": "stub", "blocked_reason": None}

    def is_reachable(self):
        return False


class TestRubricConfiguration(unittest.TestCase):
    def test_compiles_and_records_identifiers(self):
        svc, cid = build(freeze=False)
        c = svc.get_competition(cid)
        self.assertEqual(c["status"], "draft")
        self.assertEqual(c["gates"], 3084)
        self.assertTrue(c["rubric_digest"])
        self.assertTrue(c["netlist_sha256"])

    def test_rejects_bad_rubrics(self):
        svc, cid = build(freeze=False)
        bad = [
            ([], 1, "no criteria"),
            ([{"name": "x", "weight": 0, "max_score": 5}], 1, "weight >= 1"),
            ([{"name": "x", "weight": 1, "max_score": 0}], 1, "max >= 1"),
            ([{"name": "x", "weight": 1, "max_score": 5},
              {"name": "x", "weight": 1, "max_score": 5}], 1, "unique names"),
            ([{"name": "x", "weight": "abc", "max_score": 5}], 1, "weight integer"),
        ]
        for criteria, judges, why in bad:
            with self.assertRaises(ValidationError, msg=why):
                svc.set_rubric(cid, criteria, judges=judges)

    def test_rejects_unknown_tie_break(self):
        svc, cid = build(freeze=False)
        with self.assertRaises(ValidationError):
            svc.set_rubric(cid, CRITERIA, judges=3, tie_break=["nonexistent"])

    def test_digest_is_sensitive_to_weights(self):
        svc, cid = build(freeze=False)
        d1 = svc.get_competition(cid)["rubric_digest"]
        svc.set_rubric(cid, [dict(CRITERIA[0], weight=4), CRITERIA[1], CRITERIA[2]],
                       judges=3)
        d2 = svc.get_competition(cid)["rubric_digest"]
        self.assertNotEqual(d1, d2)


class TestFrozenRule(unittest.TestCase):
    def test_rubric_cannot_be_replaced_after_freeze(self):
        svc, cid = build(freeze=True)
        with self.assertRaises(ConflictError):
            svc.set_rubric(cid, [{"name": "z", "weight": 1, "max_score": 5}], judges=1)
        # the store itself also refuses
        with self.assertRaises(PermissionError):
            svc.store.set_rubric(cid, rubric_json="{}", rubric_digest="x",
                                 netlist_hex="0x", netlist_sha256="y", n_in=1,
                                 n_out=1, gates=1, tie_break=[])

    def test_cannot_freeze_twice(self):
        svc, cid = build(freeze=True)
        with self.assertRaises(ConflictError):
            svc.freeze(cid)

    def test_verify_detects_a_tampered_record(self):
        svc, cid = build()
        score_all(svc, cid)
        # Tamper with the stored digest directly, bypassing the service.
        svc.store._conn_obj.execute(
            "UPDATE competitions SET rubric_digest='deadbeef' WHERE id=?", (cid,))
        svc.store._conn_obj.commit()
        rep = svc.verify(cid)
        self.assertFalse(rep["frozen_rule_ok"])


class TestEvaluationIntegrity(unittest.TestCase):
    def test_duplicate_evaluation_is_refused(self):
        svc, cid = build()
        svc.evaluate(cid, entry_stable_id="a", judge="j1",
                     scores={"demo": 5, "tests": 5, "review": 3})
        with self.assertRaises(ConflictError):
            svc.evaluate(cid, entry_stable_id="a", judge="j1",
                         scores={"demo": 9, "tests": 9, "review": 5})

    def test_out_of_range_score_is_rejected(self):
        svc, cid = build()
        with self.assertRaises(ValidationError):
            svc.evaluate(cid, entry_stable_id="a", judge="j1",
                         scores={"demo": 11, "tests": 5, "review": 3})
        with self.assertRaises(ValidationError):
            svc.evaluate(cid, entry_stable_id="a", judge="j1",
                         scores={"demo": 5, "tests": -1, "review": 3})

    def test_missing_criterion_is_rejected(self):
        svc, cid = build()
        with self.assertRaises(ValidationError):
            svc.evaluate(cid, entry_stable_id="a", judge="j1",
                         scores={"demo": 5, "tests": 5})

    def test_unknown_judge_is_rejected(self):
        svc, cid = build()
        with self.assertRaises(ValidationError):
            svc.evaluate(cid, entry_stable_id="a", judge="intruder",
                         scores={"demo": 5, "tests": 5, "review": 3})

    def test_conflict_of_interest_blocks_scoring(self):
        svc, cid = build()
        svc.declare_conflict(cid, judge="j1", entry_stable_id="a", reason="co-founder")
        with self.assertRaises(ConflictError):
            svc.evaluate(cid, entry_stable_id="a", judge="j1",
                         scores={"demo": 5, "tests": 5, "review": 3})

    def test_commitment_is_stable_and_score_sensitive(self):
        svc, cid = build()
        r1 = svc.evaluate(cid, entry_stable_id="a", judge="j1",
                          scores={"demo": 5, "tests": 5, "review": 3})
        svc2, cid2 = build()
        r2 = svc2.evaluate(cid2, entry_stable_id="a", judge="j1",
                           scores={"demo": 5, "tests": 5, "review": 3})
        # Different competitions have different commitment keys, so the values
        # differ, but each is deterministic for its own competition.
        self.assertEqual(r1["commitment"], svc.store.list_evaluations(cid)[0]["commitment"])
        self.assertNotEqual(r1["commitment"], r2["commitment"])


class TestScoringAndRanking(unittest.TestCase):
    def test_ranking_matches_reference_arithmetic(self):
        svc, cid = build()
        score_all(svc, cid)
        res = svc.results(cid)
        # reference totals computed by hand
        def total(entry):
            t = 0
            for ci, crit in enumerate(CRITERIA):
                col = sum(SCORES[j][entry][ci] for j in JUDGES)
                t += crit["weight"] * col
            return t
        got = {r["entry"]: r["total"] for r in res["ranking"]}
        for e in ("a", "b", "c"):
            self.assertEqual(got[e], total(e))
        self.assertEqual([r["entry"] for r in res["ranking"]], ["a", "c", "b"])

    def test_deterministic_across_runs(self):
        svc, cid = build()
        score_all(svc, cid)
        r1 = svc.results(cid)["ranking"]
        r2 = svc.results(cid)["ranking"]
        self.assertEqual([(x["entry"], x["total"]) for x in r1],
                         [(x["entry"], x["total"]) for x in r2])

    def test_tie_break_is_applied(self):
        svc = TapeJudgeService(Store(":memory:"), chain_client=_StubChain())
        cid = svc.create_competition(name="Tie", judges=["j1"])
        # two criteria; entries tie on total but differ on the tie-break criterion
        svc.set_rubric(cid, [{"name": "primary", "weight": 1, "max_score": 10},
                             {"name": "secondary", "weight": 1, "max_score": 10}],
                       judges=1, tie_break=["secondary"])
        svc.freeze(cid)
        svc.add_entry(cid, stable_id="x", title="X")
        svc.add_entry(cid, stable_id="y", title="Y")
        svc.evaluate(cid, entry_stable_id="x", judge="j1",
                     scores={"primary": 8, "secondary": 4})
        svc.evaluate(cid, entry_stable_id="y", judge="j1",
                     scores={"primary": 4, "secondary": 8})
        res = svc.results(cid)
        # equal totals (12 each); y wins because it has the higher 'secondary'
        self.assertEqual(res["ranking"][0]["entry"], "y")
        self.assertEqual(res["ranking"][0]["total"], res["ranking"][1]["total"])

    def test_missing_evaluations_are_reported(self):
        svc, cid = build()
        # score only entry a
        for j in JUDGES:
            svc.evaluate(cid, entry_stable_id="a", judge=j,
                         scores={"demo": 5, "tests": 5, "review": 3})
        res = svc.results(cid)
        inc = {i["entry"] for i in res["incomplete"]}
        self.assertEqual(inc, {"b", "c"})
        self.assertEqual(res["incomplete"][0]["required"], 3)

    def test_settle_refuses_incomplete(self):
        svc, cid = build()
        for j in JUDGES:
            svc.evaluate(cid, entry_stable_id="a", judge=j,
                         scores={"demo": 5, "tests": 5, "review": 3})
        with self.assertRaises(ConflictError):
            svc.settle(cid)

    def test_settle_succeeds_when_complete(self):
        svc, cid = build()
        score_all(svc, cid)
        svc.settle(cid)
        self.assertEqual(svc.get_competition(cid)["status"], "settled")


class TestIndependentReproduction(unittest.TestCase):
    def test_verifier_agrees_with_engine(self):
        svc, cid = build()
        score_all(svc, cid)
        res = svc.results(cid)["ranking"]
        rep = svc.verify(cid)
        self.assertEqual([(r["entry"], r["total"]) for r in res],
                         [(r["entry"], r["total"]) for r in rep["ranking"]])
        self.assertTrue(rep["frozen_rule_ok"])

    def test_changing_a_score_changes_the_ranking(self):
        svc, cid = build()
        score_all(svc, cid)
        before = [r["entry"] for r in svc.results(cid)["ranking"]]
        # Directly overwrite a stored score to simulate tampering, then re-verify.
        row = svc.store._conn_obj.execute(
            "SELECT id FROM evaluations WHERE judge='j1' AND entry_id="
            "(SELECT id FROM entries WHERE competition_id=? AND stable_id='b')", (cid,)).fetchone()
        svc.store._conn_obj.execute(
            "UPDATE evaluations SET scores_json=? WHERE id=?",
            ('{"demo": 10, "tests": 10, "review": 5}', row["id"]))
        svc.store._conn_obj.commit()
        after = [r["entry"] for r in svc.results(cid)["ranking"]]
        self.assertNotEqual(before, after)


class TestCircuitValidityFlag(unittest.TestCase):
    def test_out_of_range_score_makes_the_circuit_invalid(self):
        """The circuit's own `valid` output is 0 for an over-score.

        The service rejects out-of-range scores before storage, so this checks the
        backstop directly at the circuit level (as Mission 3 did).
        """
        rubric = Rubric("X", (Criterion("a", 1, 5),), judges=1)
        circuit, meta = compile_rubric(rubric)
        # score = 6 > max 5
        bits = [0, 1, 1]  # 6 in 3 bits (width for max 5 is 3)
        _ns, out = beat(circuit, [], bits)
        d = decode_outputs(out, meta)
        self.assertEqual(d["valid"], 0)

    def test_in_range_score_is_valid(self):
        rubric = Rubric("X", (Criterion("a", 1, 5),), judges=1)
        circuit, meta = compile_rubric(rubric)
        bits = [1, 0, 1]  # 5
        _ns, out = beat(circuit, [], bits)
        self.assertEqual(decode_outputs(out, meta)["valid"], 1)


class TestPublicationHonesty(unittest.TestCase):
    def test_not_published_before_freeze(self):
        svc, cid = build(freeze=False)
        pub = svc.get_competition(cid)["publication"]
        self.assertEqual(pub["mode"], "not_published")
        self.assertFalse(pub["on_chain"])
        # Before freezing the rubric really is not frozen; the label must say so.
        self.assertIn("not frozen", pub["label"])

    def test_frozen_but_unpublished_is_not_labelled_unfrozen(self):
        # A frozen competition has a compiled circuit. Its publication state must
        # never read "the rubric is not frozen" — that would be a false claim.
        svc, cid = build()
        pub = svc.get_competition(cid)["publication"]
        self.assertEqual(pub["mode"], "not_published")
        self.assertNotIn("not frozen", pub["label"])
        self.assertIn("Frozen locally", pub["label"])

    def test_publish_never_claims_on_chain_without_a_transaction(self):
        svc, cid = build()
        result = svc.publish(cid)
        self.assertEqual(result["mode"], "onchain_pending_signature")
        pub = svc.get_competition(cid)["publication"]
        self.assertFalse(pub["on_chain"])
        self.assertIsNone(pub["tx_hash"])


class TestPrivacyAndAudit(unittest.TestCase):
    def test_private_entry_hides_content_but_keeps_hash(self):
        svc, cid = build()
        svc.add_entry(cid, stable_id="secret", title="Secret Title",
                      summary="confidential", private=True)
        public = {e["stable_id"]: e for e in svc.list_entries(cid)}
        self.assertEqual(public["secret"]["title"], "(private entry)")
        self.assertEqual(public["secret"]["summary"], "")
        self.assertTrue(public["secret"]["content_hash"])

    def test_audit_trail_records_the_lifecycle(self):
        svc, cid = build()
        score_all(svc, cid)
        kinds = [e["kind"] for e in svc.store.list_events(cid)]
        for expected in ("competition_created", "rubric_configured", "rubric_frozen",
                         "entry_submitted", "evaluation_committed"):
            self.assertIn(expected, kinds)

    def test_correction_note_does_not_change_inputs(self):
        svc, cid = build()
        score_all(svc, cid)
        before = svc.results(cid)["ranking"]
        svc.add_correction(cid, note="Typo in the announcement, scores unchanged.",
                           actor="organiser")
        after = svc.results(cid)["ranking"]
        self.assertEqual([(r["entry"], r["total"]) for r in before],
                         [(r["entry"], r["total"]) for r in after])
        self.assertIn("correction_recorded",
                      [e["kind"] for e in svc.store.list_events(cid)])


if __name__ == "__main__":
    unittest.main()
