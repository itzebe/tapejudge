"""TapeJudge rubric model and circuit compiler.

A *rubric* is the frozen rule of a competition: the criteria, their integer
weights, the maximum each criterion can score, how many judges there are, and
the tie-break order. TapeJudge compiles a rubric into a TapeOut netlist so the
rule can be taped out once and re-run by anyone.

The circuit is combinational (NAND only, no LATCH), so `eval()` returns the
result and the computation is a pure function of its inputs — the property the
product depends on.

Circuit I/O
-----------
Inputs (little-endian bit packing, LSB first), in order:
    for each judge j, for each criterion c:  score[j][c]  (width_c bits)

Outputs, in order (the trailing signals, TAP-02 §4):
    valid      : 1 iff every score is <= its criterion maximum      (1 bit)
    sub_0..sub_{C-1} : per-criterion weighted subtotal  (total_width each)
    total      : weighted sum  sum_c w_c * sum_j score[j][c]        (total_width)

Signals are emitted so the trailing `n_out` signals are exactly this order.

`valid` is 0 when any judge submitted an out-of-range score, so a settlement
contract can refuse to pay on an invalid evaluation instead of silently
accepting a malformed one.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .netlist import Circuit, NetlistBuilder, add, decode_uint, gt, mul_const


@dataclass(frozen=True)
class Criterion:
    name: str
    weight: int          # integer weight, >= 1
    max_score: int       # inclusive maximum a judge may give, >= 1


@dataclass(frozen=True)
class Rubric:
    competition: str
    criteria: tuple[Criterion, ...]
    judges: int          # number of judges on the panel (frozen with the rule)
    # tie-break: criterion names in priority order (defaults to declaration order)
    tie_break: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.criteria:
            raise ValueError("a rubric needs at least one criterion")
        if self.judges < 1:
            raise ValueError("a rubric needs at least one judge")
        for c in self.criteria:
            if c.weight < 1:
                raise ValueError(f"criterion {c.name!r}: weight must be >= 1")
            if c.max_score < 1:
                raise ValueError(f"criterion {c.name!r}: max_score must be >= 1")
        names = [c.name for c in self.criteria]
        if len(set(names)) != len(names):
            raise ValueError("criterion names must be unique")
        tb = self.tie_break or tuple(names)
        for n in tb:
            if n not in names:
                raise ValueError(f"tie-break names unknown criterion {n!r}")
        object.__setattr__(self, "tie_break", tb)

    # -- widths ------------------------------------------------------------- #
    def _bits_for(self, n: int) -> int:
        return max(1, n.bit_length())

    @property
    def score_widths(self) -> list[int]:
        return [self._bits_for(c.max_score) for c in self.criteria]

    @property
    def total_width(self) -> int:
        max_total = sum(c.weight * c.max_score * self.judges for c in self.criteria)
        return self._bits_for(max_total)

    @property
    def n_in(self) -> int:
        return self.judges * sum(self.score_widths)

    @property
    def n_out(self) -> int:
        return self.total_width + 1 + len(self.criteria) * self.total_width

    # -- serialisation ------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "competition": self.competition,
            "judges": self.judges,
            "criteria": [asdict(c) for c in self.criteria],
            "tie_break": list(self.tie_break),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        """SHA-256 of the canonical rubric JSON — the human-readable rule's hash."""
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @staticmethod
    def from_dict(d: dict) -> Rubric:
        return Rubric(
            competition=d["competition"],
            criteria=tuple(Criterion(**c) for c in d["criteria"]),
            judges=int(d["judges"]),
            tie_break=tuple(d.get("tie_break", ())),
        )


# --------------------------------------------------------------------------- #
# Compiler: rubric -> netlist
# --------------------------------------------------------------------------- #

def _sum_scores_for_criterion(b: NetlistBuilder, rubric: Rubric, c_index: int) -> list[int]:
    """Sum one criterion's scores across all judges, as a total_width vector."""
    w = rubric.total_width
    acc = [b.zero] * w
    width = rubric.score_widths[c_index]
    for j in range(rubric.judges):
        base = j * sum(rubric.score_widths) + sum(rubric.score_widths[:c_index])
        bits = [b.input(base + k) for k in range(width)]
        bits = bits + [b.zero] * (w - len(bits))
        acc, _ = add(b, acc, bits)
    return acc


def compile_rubric(rubric: Rubric) -> tuple[Circuit, dict]:
    """Compile a rubric into a combinational circuit.

    The circuit's trailing `n_out` signals are, in order:
        valid, sub_0 .. sub_{C-1}, total
    Returns (circuit, metadata) so a verifier can decode outputs without guessing.
    """
    b = NetlistBuilder(rubric.n_in)
    w = rubric.total_width
    cw = rubric.score_widths

    # -- validity: every input score must be <= its criterion maximum -------- #
    valid_bits = []
    for j in range(rubric.judges):
        for ci, crit in enumerate(rubric.criteria):
            base = j * sum(cw) + sum(cw[:ci])
            bits = [b.input(base + k) for k in range(cw[ci])]
            le = b.not_(gt(b, bits, _const_bits(b, crit.max_score, cw[ci])))
            valid_bits.append(le)
    valid = b.and_all(valid_bits)

    # -- per-criterion weighted subtotals ----------------------------------- #
    subtotals = []
    for ci, crit in enumerate(rubric.criteria):
        s = _sum_scores_for_criterion(b, rubric, ci)
        subtotals.append(mul_const(b, s, crit.weight, w))

    # -- total: sum of subtotals -------------------------------------------- #
    total = [b.zero] * w
    for st in subtotals:
        total, _ = add(b, total, st)

    # The circuit's outputs are its *trailing* signals, in index order. Emit the
    # result fields as identity copies so the last n_out signals are exactly:
    #   total[w], valid, sub_0[w], .., sub_{C-1}[w]
    # Identity is two NANDs, so emit every intermediate first and every final
    # copy second; otherwise a trailing intermediate would be mistaken for an
    # output. See _emit_outputs.
    result = list(total) + [valid]
    for st in subtotals:
        result += list(st)
    _emit_outputs(b, result)

    circuit = b.build(rubric.n_out)
    # Every signal the elements produce is an output here (we emit exactly
    # n_out signals), so the output bit order is the emission order:
    #   total[0..w-1], valid, sub_0[0..w-1], .., sub_{C-1}[0..w-1]
    meta = {
        "n_in": rubric.n_in,
        "n_out": rubric.n_out,
        "total_width": w,
        "score_widths": cw,
        "judges": rubric.judges,
        "criteria": [c.name for c in rubric.criteria],
        "weights": [c.weight for c in rubric.criteria],
        "max_scores": [c.max_score for c in rubric.criteria],
        "tie_break": list(rubric.tie_break),
        "output_layout": "total[w] | valid[1] | sub_0[w] | .. | sub_{C-1}[w]",
        "rubric_digest": rubric.digest(),
    }
    return circuit, meta


def decode_outputs(out_bits: list[int], meta: dict) -> dict:
    """Decode a circuit's raw output bits into named fields."""
    w = meta["total_width"]
    n_crit = len(meta["criteria"])
    if len(out_bits) < w + 1 + n_crit * w:
        raise ValueError("output vector is shorter than the circuit's n_out")
    total = decode_uint(out_bits[0:w])
    valid = out_bits[w]
    subs = []
    for ci in range(n_crit):
        base = w + 1 + ci * w
        subs.append(decode_uint(out_bits[base:base + w]))
    return {"total": total, "valid": valid, "subtotals": subs}


def _emit_outputs(b: NetlistBuilder, signals: list[int]) -> list[int]:
    """Make `signals` the circuit's trailing output signals, in order.

    Identity is NOT(NOT s) = two NANDs. Emitting all intermediates first and all
    final copies second guarantees the trailing `len(signals)` signals are the
    identity copies, in the same order as `signals`.
    """
    tmp = [b.not_(s) for s in signals]
    return [b.not_(t) for t in tmp]


def _const_bits(b: NetlistBuilder, value: int, width: int) -> list[int]:
    return [b.one if (value >> k) & 1 else b.zero for k in range(width)]
