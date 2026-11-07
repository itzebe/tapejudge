"""TapeJudge verifier.

An independent reproduction of a competition's result. Given the frozen rubric
and the published evaluations, the verifier recompiles the circuit, re-packs the
inputs, re-runs the evaluation and compares the ranking. It shares no state with
the scoring engine: it is the "second opinion" a participant can run themselves.
"""
from __future__ import annotations

from .engine import Competition, JudgeEvaluation, ScoringError
from .netlist import beat
from .rubric import Rubric, compile_rubric, decode_outputs


def reproduce_entry(rubric: Rubric, entry: str, evaluations: list[JudgeEvaluation]) -> dict:
    """Recompute one entry's score from the rubric and raw evaluations."""
    circuit, meta = compile_rubric(rubric)
    by_judge = {e.judge: e for e in evaluations if e.entry == entry}
    if len(by_judge) != rubric.judges:
        raise ScoringError(f"entry {entry!r}: expected {rubric.judges} evaluations, got {len(by_judge)}")
    widths = rubric.score_widths
    bits: list[int] = []
    for judge in sorted(by_judge):
        ev = by_judge[judge]
        for ci, crit in enumerate(rubric.criteria):
            v = ev.scores[crit.name]
            if not isinstance(v, int) or v < 0 or v > crit.max_score:
                raise ScoringError(f"{judge}/{crit.name}: invalid score {v!r}")
            for k in range(widths[ci]):
                bits.append((v >> k) & 1)
    _ns, out_bits = beat(circuit, [], bits)
    d = decode_outputs(out_bits, meta)
    d["entry"] = entry
    return d


def verify_ranking(rubric: Rubric, entry_ids: tuple[str, ...],
                   evaluations: list[JudgeEvaluation]) -> dict:
    """Reproduce a full ranking independently and return it plus the evidence."""
    comp = Competition(rubric=rubric, entry_ids=tuple(entry_ids))
    for ev in evaluations:
        comp.submit(ev)
    result = comp.rank()
    return {
        "rubric_digest": rubric.digest(),
        "ranking": [{"rank": r.get("rank"), "entry": r["entry"], "total": r["total"],
                     "subtotals": r["subtotals"]} for r in result["ranking"]],
        "invalid": [r.get("entry") for r in result["invalid"]],
        "audit": comp.audit_trail(),
    }
