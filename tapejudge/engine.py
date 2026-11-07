"""TapeJudge scoring engine.

Ties a frozen rubric to a competition, accepts judge evaluations, rejects
malformed or duplicated submissions, runs the compiled circuit, ranks entries
with a deterministic tie-break, and produces an audit trail.

Two execution backends implement the same `score()` contract:

* `LocalCircuitBackend` — runs the compiled circuit in-process (this POC).
* a chain backend (not implemented here) — calls `eval(circuitId, inputs)` on an
  X Layer processor, which is a free read. Because both backends evaluate the
  same TAP-02 semantics, the local result and the on-chain result must agree;
  the POC ships a `--verify-chain` mode that checks this against the real chain
  for the sample circuits (see ../evidence/CHAIN_VERIFICATION.md).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .netlist import beat, encode
from .rubric import Rubric, compile_rubric, decode_outputs


class ScoringError(ValueError):
    pass


@dataclass
class JudgeEvaluation:
    judge: str                  # judge id / address / name
    entry: str                  # entry id
    scores: dict[str, int]      # criterion name -> integer score
    nonce: str = ""             # optional client nonce to make re-submits explicit


@dataclass
class Competition:
    rubric: Rubric
    entry_ids: tuple[str, ...] = ()
    # judged evaluations, keyed by (judge, entry)
    evaluations: dict[tuple[str, str], JudgeEvaluation] = field(default_factory=dict)
    frozen: bool = True
    _circuit: object = None
    _meta: dict = None

    def __post_init__(self):
        self._circuit, self._meta = compile_rubric(self.rubric)

    # -- evaluation intake -------------------------------------------------- #
    def submit(self, ev: JudgeEvaluation) -> None:
        if ev.entry not in self.entry_ids:
            raise ScoringError(f"unknown entry {ev.entry!r}")
        if not isinstance(ev.scores, dict):
            raise ScoringError("scores must be a mapping of criterion -> integer")
        unknown = set(ev.scores) - {c.name for c in self.rubric.criteria}
        if unknown:
            raise ScoringError(f"unknown criteria: {sorted(unknown)}")
        for c in self.rubric.criteria:
            if c.name not in ev.scores:
                raise ScoringError(f"missing score for criterion {c.name!r}")
            v = ev.scores[c.name]
            if not isinstance(v, int) or isinstance(v, bool):
                raise ScoringError(f"{c.name!r}: score must be an integer")
            if v < 0 or v > c.max_score:
                raise ScoringError(
                    f"{c.name!r}: score {v} outside [0, {c.max_score}]")
        key = (ev.judge, ev.entry)
        if key in self.evaluations:
            prev = self.evaluations[key]
            # A judge may not silently overwrite a submitted score. Re-submitting
            # the identical evaluation is idempotent; anything else is rejected.
            if prev.scores == ev.scores:
                return
            raise ScoringError(
                f"judge {ev.judge!r} already scored entry {ev.entry!r}; "
                "scores are immutable once submitted")
        self.evaluations[key] = ev

    # -- input packing ------------------------------------------------------ #
    def _pack_inputs(self, entry: str) -> tuple[list[int], list[JudgeEvaluation]]:
        """Pack all judges' scores for one entry into the circuit's input bits.

        Input order is judge-major: for each judge (in ascending id order), for
        each criterion (in declaration order), the score's bits LSB-first. The
        judge order is recorded so a verifier reproduces the same packing.
        """
        widths = self.rubric.score_widths
        bits: list[int] = []
        used: list[JudgeEvaluation] = []
        by_judge = {}
        for (judge, ent), ev in self.evaluations.items():
            if ent == entry:
                by_judge.setdefault(judge, ev)
        judges = sorted(by_judge)  # deterministic judge order
        if len(judges) != self.rubric.judges:
            raise ScoringError(
                f"entry {entry!r} has {len(judges)} of {self.rubric.judges} required evaluations")
        for judge in judges:
            ev = by_judge[judge]
            used.append(ev)
            for ci, crit in enumerate(self.rubric.criteria):
                v = ev.scores[crit.name]
                w = widths[ci]
                for k in range(w):
                    bits.append((v >> k) & 1)
        return bits, used

    # -- scoring ------------------------------------------------------------ #
    def score_entry(self, entry: str) -> dict:
        bits, used = self._pack_inputs(entry)
        if len(bits) != self.rubric.n_in:
            raise ScoringError("packed input length does not match the circuit")
        _new_state, out_bits = beat(self._circuit, [], bits)
        decoded = decode_outputs(out_bits, self._meta)
        decoded["entry"] = entry
        decoded["evaluations"] = [
            {"judge": e.judge, "scores": e.scores, "nonce": e.nonce} for e in used
        ]
        # a judge's weight contribution is recoverable from the subtotals
        return decoded

    def rank(self) -> list[dict]:
        results = []
        for entry in self.entry_ids:
            try:
                results.append(self.score_entry(entry))
            except ScoringError as e:
                results.append({"entry": entry, "error": str(e)})
        # invalid entries (valid == 0) are excluded from ranking
        valid = [r for r in results if r.get("valid") == 1]
        invalid = [r for r in results if "error" not in r and r.get("valid") != 1]

        order = {c.name: i for i, c in enumerate(self.rubric.criteria)}
        tb = [order[n] for n in self.rubric.tie_break]

        def sort_key(r):
            # primary: total (descending). tie-break: criterion subtotals in the
            # declared priority order (descending), then entry id for stability.
            key = [-r["total"]]
            for ci in tb:
                key.append(-r["subtotals"][ci])
            key.append(r["entry"])
            return tuple(key)

        valid.sort(key=sort_key)
        for i, r in enumerate(valid):
            r["rank"] = i + 1
        return {"ranking": valid, "invalid": invalid, "all": results}

    # -- audit -------------------------------------------------------------- #
    def audit_trail(self) -> dict:
        return {
            "competition": self.rubric.competition,
            "rubric": self.rubric.to_dict(),
            "rubric_digest": self.rubric.digest(),
            "circuit": {
                "n_in": self._meta["n_in"],
                "n_out": self._meta["n_out"],
                "gates": self._circuit.n_gates,
                "netlist_sha256": hashlib.sha256(encode(self._circuit.elements)).hexdigest(),
                "netlist_hex": "0x" + encode(self._circuit.elements).hex(),
            },
            "judges": self.rubric.judges,
            "entries": list(self.entry_ids),
            "evaluations": [
                {"judge": k[0], "entry": k[1], "scores": v.scores, "nonce": v.nonce}
                for k, v in sorted(self.evaluations.items())
            ],
        }
