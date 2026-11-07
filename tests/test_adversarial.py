"""Adversarial tests: try to break the guarantee.

Each test states an attack and asserts the system's response. Where the honest
answer is "the system cannot prevent this", the test documents that instead of
pretending otherwise.
"""
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
    lifecycle,
)


def r():
    return Rubric("Adv", (Criterion("quality", 2, 10), Criterion("docs", 1, 5)), judges=3)


class TestMaliciousInputs(unittest.TestCase):
    def test_bool_is_not_an_integer_score(self):
        comp = Competition(rubric=r(), entry_ids=("A",))
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", {"quality": True, "docs": 1}))

    def test_huge_score_rejected(self):
        comp = Competition(rubric=r(), entry_ids=("A",))
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", {"quality": 10 ** 9, "docs": 1}))

    def test_non_dict_scores_rejected(self):
        comp = Competition(rubric=r(), entry_ids=("A",))
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", [1, 2]))  # type: ignore[arg-type]

    def test_criterion_name_confusion_rejected(self):
        comp = Competition(rubric=r(), entry_ids=("A",))
        with self.assertRaises(ScoringError):
            comp.submit(JudgeEvaluation("J0", "A", {"Quality": 5, "docs": 1}))  # case mismatch

    def test_extra_judge_cannot_dilute_or_add(self):
        # A panel of 3 is frozen; submitting only 2 evaluations must leave the
        # entry unranked (an error), never silently scored with a smaller panel.
        comp = Competition(rubric=r(), entry_ids=("A",))
        comp.submit(JudgeEvaluation("J0", "A", {"quality": 5, "docs": 1}))
        comp.submit(JudgeEvaluation("J1", "A", {"quality": 5, "docs": 1}))
        res = comp.rank()
        self.assertEqual(res["ranking"], [])
        self.assertTrue(any("error" in r for r in res["all"]))

    def test_scores_wrap_is_flagged_not_silently_accepted(self):
        # A score that overflows its bit field must not be silently accepted by
        # the circuit. The circuit flags any representable score above the max.
        from tapejudge.netlist import beat
        from tapejudge.rubric import compile_rubric, decode_outputs
        rr = Rubric("W", (Criterion("c", 1, 5),), judges=1)
        c, meta = compile_rubric(rr)
        for v in range(8):  # 3-bit field
            bits = [(v >> k) & 1 for k in range(meta["score_widths"][0])]
            _ns, out = beat(c, [], bits)
            d = decode_outputs(out, meta)
            self.assertEqual(d["valid"], 1 if v <= 5 else 0, v)


class TestConflictingEvaluations(unittest.TestCase):
    def test_judge_cannot_flip_a_committed_score(self):
        rec = lifecycle.freeze(r(), [f"J{i}" for i in range(3)], ["A"])
        secret = b"k"
        lifecycle.commit_evaluation(rec, JudgeEvaluation("J0", "A", {"quality": 5, "docs": 1}), secret)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.commit_evaluation(rec, JudgeEvaluation("J0", "A", {"quality": 1, "docs": 1}), secret)

    def test_two_judges_same_entry_both_counted(self):
        comp = Competition(rubric=r(), entry_ids=("A",))
        for j in range(3):
            comp.submit(JudgeEvaluation(f"J{j}", "A", {"quality": 4, "docs": 2}))
        d = comp.score_entry("A")
        self.assertEqual(d["total"], 2 * (4 * 3) + 1 * (2 * 3))

    def test_partial_panel_is_not_silently_padded(self):
        comp = Competition(rubric=r(), entry_ids=("A",))
        comp.submit(JudgeEvaluation("J0", "A", {"quality": 4, "docs": 2}))
        with self.assertRaises(ScoringError):
            comp.score_entry("A")


class TestEdgeCases(unittest.TestCase):
    def test_single_judge_single_criterion(self):
        rr = Rubric("S", (Criterion("only", 1, 1),), judges=1)
        comp = Competition(rubric=rr, entry_ids=("A", "B"))
        comp.submit(JudgeEvaluation("J", "A", {"only": 1}))
        comp.submit(JudgeEvaluation("J", "B", {"only": 0}))
        res = comp.rank()
        self.assertEqual(res["ranking"][0]["entry"], "A")

    def test_all_zero_scores(self):
        comp = Competition(rubric=r(), entry_ids=("A",))
        for j in range(3):
            comp.submit(JudgeEvaluation(f"J{j}", "A", {"quality": 0, "docs": 0}))
        self.assertEqual(comp.score_entry("A")["total"], 0)

    def test_many_judges_and_criteria_compile(self):
        rr = Rubric("Big", tuple(Criterion(f"c{i}", i + 1, 10) for i in range(6)), judges=5)
        comp = Competition(rubric=rr, entry_ids=("A",))
        for j in range(5):
            comp.submit(JudgeEvaluation(f"J{j}", "A", {f"c{i}": 10 for i in range(6)}))
        d = comp.score_entry("A")
        self.assertEqual(d["total"], sum((i + 1) * 10 * 5 for i in range(6)))
        self.assertEqual(d["valid"], 1)

    def test_wide_score_field(self):
        rr = Rubric("Wide", (Criterion("c", 1, 1000),), judges=1)
        comp = Competition(rubric=rr, entry_ids=("A",))
        comp.submit(JudgeEvaluation("J", "A", {"c": 1000}))
        self.assertEqual(comp.score_entry("A")["total"], 1000)
        self.assertEqual(comp.score_entry("A")["valid"], 1)


class TestHonestLimits(unittest.TestCase):
    """These document what the system does NOT protect against."""

    def test_rubric_can_be_redefined_in_a_new_competition(self):
        # Freezing binds one competition. A new competition may use any rubric;
        # the point is that an *existing* one cannot change, not that rules are
        # globally fixed.
        a = lifecycle.freeze(r(), ["J0"], ["A"])
        b = lifecycle.freeze(Rubric("Adv", (Criterion("quality", 5, 10),), judges=1), ["J0"], ["A"])
        self.assertNotEqual(a.rubric_digest, b.rubric_digest)

    def test_commitment_key_is_the_trust_anchor(self):
        # Without the secret, a commitment cannot be forged; with the secret,
        # an evaluation can be rewritten. The secret is the trust anchor, so it
        # must live with the organiser / judge, not in a public page.
        rec = lifecycle.freeze(r(), ["J0"], ["A"])
        ev = JudgeEvaluation("J0", "A", {"quality": 5, "docs": 1})
        lifecycle.commit_evaluation(rec, ev, b"real")
        self.assertFalse(lifecycle.verify_commitment(rec, ev, b"wrong"))

    def test_circuit_does_not_validate_judge_identity(self):
        # The engine accepts any judge id string. Identity is out of the MVP's
        # scope; it needs a key registry (see the product spec, limitation C).
        comp = Competition(rubric=r(), entry_ids=("A",))
        for j in ("attacker", "attacker2", "attacker3"):
            comp.submit(JudgeEvaluation(j, "A", {"quality": 10, "docs": 5}))
        self.assertEqual(comp.score_entry("A")["total"], 2 * 30 + 1 * 15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
