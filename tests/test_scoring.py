"""Scoring engine, verifier and lifecycle tests.

These cover the product's core claims: a deterministic weighted sum, a
reproducible result, immutable rules after freeze, immutable judge scores,
rejection of malformed/duplicate evaluations, deterministic tie-breaks, and an
independent verifier that agrees with the engine.
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge import (
    Competition,
    Criterion,
    JudgeEvaluation,
    Rubric,
    ScoringError,
    compile_rubric,
    lifecycle,
)
from tapejudge.netlist import beat
from tapejudge.rubric import decode_outputs


def rubric_2x2():
    return Rubric("Demo", (Criterion("quality", 3, 10), Criterion("docs", 1, 5)), judges=2)


def expected_total(rubric: Rubric, scores_by_judge: dict) -> int:
    total = 0
    for c in rubric.criteria:
        col = sum(scores_by_judge[j][c.name] for j in scores_by_judge)
        total += c.weight * col
    return total


class TestRubric(unittest.TestCase):
    def test_widths_and_io(self):
        r = rubric_2x2()
        self.assertEqual(r.score_widths, [4, 3])   # ceil(log2(10+1)), ceil(log2(5+1))
        self.assertEqual(r.n_in, 2 * (4 + 3))
        self.assertEqual(r.total_width, 7)         # max total 3*10*2 + 1*5*2 = 70 -> 7 bits
        self.assertEqual(r.n_out, 7 + 1 + 2 * 7)

    def test_digest_is_stable_and_sensitive(self):
        r1 = rubric_2x2()
        r2 = rubric_2x2()
        self.assertEqual(r1.digest(), r2.digest())
        r3 = Rubric("Demo", (Criterion("quality", 3, 10), Criterion("docs", 2, 5)), judges=2)
        self.assertNotEqual(r1.digest(), r3.digest())

    def test_rejects_bad_rubric(self):
        with self.assertRaises(ValueError):
            Rubric("x", (Criterion("a", 0, 5),), judges=1)
        with self.assertRaises(ValueError):
            Rubric("x", (Criterion("a", 1, 0),), judges=1)
        with self.assertRaises(ValueError):
            Rubric("x", (), judges=1)
        with self.assertRaises(ValueError):
            Rubric("x", (Criterion("a", 1, 5), Criterion("a", 1, 5)), judges=1)


class TestCircuitScoring(unittest.TestCase):
    def test_weighted_sum_matches_reference(self):
        r = rubric_2x2()
        c, meta = compile_rubric(r)
        for j0, j1 in itertools.product(
                itertools.product(range(11), range(6)), repeat=2):
            s = {"J0": {"quality": j0[0], "docs": j0[1]},
                 "J1": {"quality": j1[0], "docs": j1[1]}}
            bits = []
            for scores in (j0, j1):
                for v, w in zip(scores, meta["score_widths"]):
                    bits += [(v >> k) & 1 for k in range(w)]
            _ns, out = beat(c, [], bits)
            d = decode_outputs(out, meta)
            self.assertEqual(d["total"], expected_total(r, s), (j0, j1))
            self.assertEqual(d["valid"], 1)
            self.assertEqual(d["subtotals"][0], 3 * (j0[0] + j1[0]))
            self.assertEqual(d["subtotals"][1], 1 * (j0[1] + j1[1]))

    def test_determinism(self):
        r = rubric_2x2()
        c, meta = compile_rubric(r)
        bits = [1, 0, 1, 1, 0, 1, 0] * 2
        outs = set()
        for _ in range(5):
            _ns, out = beat(c, [], bits)
            outs.add(tuple(out))
        self.assertEqual(len(outs), 1)

    def test_changing_an_input_changes_the_result(self):
        r = rubric_2x2()
        c, meta = compile_rubric(r)
        base = [1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0]
        _ns, o1 = beat(c, [], base)
        flipped = list(base)
        flipped[0] ^= 1
        _ns, o2 = beat(c, [], flipped)
        self.assertNotEqual(o1, o2)

    def test_over_score_is_invalid(self):
        r = rubric_2x2()
        c, meta = compile_rubric(r)
        # docs max is 5; use 6 (representable in 3 bits)
        bits = []
        for scores in ([8, 4], [6, 6]):
            for v, w in zip(scores, meta["score_widths"]):
                bits += [(v >> k) & 1 for k in range(w)]
        _ns, out = beat(c, [], bits)
        self.assertEqual(decode_outputs(out, meta)["valid"], 0)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.r = rubric_2x2()
        self.entries = ("A", "B", "C")

    def build(self):
        return Competition(rubric=self.r, entry_ids=self.entries)

    def test_ranking_and_totals(self):
        comp = self.build()
        data = {
            "J0": {"A": [8, 4], "B": [5, 3], "C": [9, 2]},
            "J1": {"A": [6, 5], "B": [7, 2], "C": [4, 1]},
        }
        for j, per in data.items():
            for e, sc in per.items():
                comp.submit(JudgeEvaluation(j, e, {"quality": sc[0], "docs": sc[1]}))
        res = comp.rank()
        rank = {r["entry"]: r for r in res["ranking"]}
        self.assertEqual(rank["A"]["total"], expected_total(self.r, {"J0": {"quality": 8, "docs": 4}, "J1": {"quality": 6, "docs": 5}}))
        self.assertEqual(res["ranking"][0]["entry"], "A")  # A has the highest total
        self.assertEqual([r["rank"] for r in res["ranking"]], [1, 2, 3])

    def test_duplicate_evaluation_rejected(self):
        comp = self.build()
        comp.submit(JudgeEvaluation("J0", "A", {"quality": 8, "docs": 4}))
        # identical resubmit is idempotent
        comp.submit(JudgeEvaluation("J0", "A", {"quality": 8, "docs": 4}))
        # a changed score is rejected
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", {"quality": 9, "docs": 4}))

    def test_invalid_submissions_rejected(self):
        comp = self.build()
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "Z", {"quality": 1, "docs": 1}))  # unknown entry
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", {"quality": 1}))            # missing criterion
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", {"quality": 1, "docs": 1, "extra": 1}))
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", {"quality": 11, "docs": 1}))  # over max
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", {"quality": -1, "docs": 1}))  # negative
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", {"quality": "5", "docs": 1}))  # not int

    def test_incomplete_evaluation_excluded(self):
        comp = self.build()
        comp.submit(JudgeEvaluation("J0", "A", {"quality": 8, "docs": 4}))  # only 1 of 2 judges
        res = comp.rank()
        self.assertEqual([r["entry"] for r in res["ranking"]], [])
        self.assertTrue(any("error" in r for r in res["all"]))

    def test_tie_break_by_priority_criterion(self):
        # two entries with equal totals; the first tie-break criterion decides
        r = Rubric("T", (Criterion("quality", 1, 10), Criterion("docs", 1, 10)),
                   judges=1, tie_break=("docs", "quality"))
        comp = Competition(rubric=r, entry_ids=("X", "Y"))
        comp.submit(JudgeEvaluation("J", "X", {"quality": 9, "docs": 5}))
        comp.submit(JudgeEvaluation("J", "Y", {"quality": 5, "docs": 9}))
        res = comp.rank()
        self.assertEqual(res["ranking"][0]["entry"], "Y")  # higher docs wins the tie

    def test_tie_break_is_deterministic(self):
        r = Rubric("T", (Criterion("a", 1, 10),), judges=1)
        comp = Competition(rubric=r, entry_ids=("P", "Q"))
        comp.submit(JudgeEvaluation("J", "P", {"a": 5}))
        comp.submit(JudgeEvaluation("J", "Q", {"a": 5}))
        first = [x["entry"] for x in comp.rank()["ranking"]]
        second = [x["entry"] for x in comp.rank()["ranking"]]
        self.assertEqual(first, second)


class TestIndependentVerifier(unittest.TestCase):
    def test_verifier_reproduces_engine(self):
        from tapejudge import verify_ranking
        r = rubric_2x2()
        evals = [
            JudgeEvaluation("J0", "A", {"quality": 8, "docs": 4}),
            JudgeEvaluation("J1", "A", {"quality": 6, "docs": 5}),
            JudgeEvaluation("J0", "B", {"quality": 5, "docs": 3}),
            JudgeEvaluation("J1", "B", {"quality": 7, "docs": 2}),
        ]
        comp = Competition(rubric=r, entry_ids=("A", "B"))
        for e in evals:
            comp.submit(e)
        engine_rank = [(x["entry"], x["total"]) for x in comp.rank()["ranking"]]

        rep = verify_ranking(r, ("A", "B"), evals)
        verifier_rank = [(x["entry"], x["total"]) for x in rep["ranking"]]
        self.assertEqual(engine_rank, verifier_rank)

    def test_verifier_detects_a_changed_score(self):
        from tapejudge import verify_ranking
        r = rubric_2x2()
        evals = [
            JudgeEvaluation("J0", "A", {"quality": 8, "docs": 4}),
            JudgeEvaluation("J1", "A", {"quality": 6, "docs": 5}),
        ]
        base = verify_ranking(r, ("A",), evals)["ranking"][0]["total"]
        tampered = verify_ranking(r, ("A",), [
            JudgeEvaluation("J0", "A", {"quality": 9, "docs": 4}),
            JudgeEvaluation("J1", "A", {"quality": 6, "docs": 5}),
        ])["ranking"][0]["total"]
        self.assertNotEqual(base, tampered)


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.r = rubric_2x2()
        self.secret = b"competition-commitment-key"

    def test_freeze_records_digests(self):
        rec = lifecycle.freeze(self.r, ["J0", "J1"], ["A", "B"])
        self.assertEqual(rec.rubric_digest, self.r.digest())
        self.assertTrue(rec.netlist_sha256)
        self.assertTrue(lifecycle.verify_audit(rec)["ok"])

    def test_modified_rubric_rejected(self):
        rec = lifecycle.freeze(self.r, ["J0", "J1"], ["A", "B"])
        changed = Rubric("Demo", (Criterion("quality", 3, 10), Criterion("docs", 2, 5)), judges=2)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.assert_unchanged(rec, changed)
        # the unchanged rubric passes
        lifecycle.assert_unchanged(rec, rubric_2x2())

    def test_commitment_immutability(self):
        rec = lifecycle.freeze(self.r, ["J0", "J1"], ["A", "B"])
        ev = JudgeEvaluation("J0", "A", {"quality": 8, "docs": 4})
        c1 = lifecycle.commit_evaluation(rec, ev, self.secret)
        # identical resubmit returns the same commitment
        self.assertEqual(lifecycle.commit_evaluation(rec, ev, self.secret), c1)
        # changed score is rejected
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.commit_evaluation(rec, JudgeEvaluation("J0", "A", {"quality": 9, "docs": 4}), self.secret)

    def test_commitment_verification(self):
        rec = lifecycle.freeze(self.r, ["J0", "J1"], ["A", "B"])
        ev = JudgeEvaluation("J0", "A", {"quality": 8, "docs": 4})
        lifecycle.commit_evaluation(rec, ev, self.secret)
        self.assertTrue(lifecycle.verify_commitment(rec, ev, self.secret))
        self.assertFalse(lifecycle.verify_commitment(rec, JudgeEvaluation("J0", "A", {"quality": 9, "docs": 4}), self.secret))

    def test_unknown_judge_and_settled(self):
        rec = lifecycle.freeze(self.r, ["J0", "J1"], ["A", "B"])
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.commit_evaluation(rec, JudgeEvaluation("ZZ", "A", {"quality": 1, "docs": 1}), self.secret)
        for j in ("J0", "J1"):
            for e in ("A", "B"):
                lifecycle.commit_evaluation(rec, JudgeEvaluation(j, e, {"quality": 5, "docs": 2}), self.secret)
        lifecycle.settle(rec)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.commit_evaluation(rec, JudgeEvaluation("J0", "A", {"quality": 6, "docs": 2}), self.secret)


if __name__ == "__main__":
    unittest.main(verbosity=2)
