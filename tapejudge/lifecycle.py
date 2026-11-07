"""TapeJudge competition lifecycle and the honest integrity model.

The mission requires three things to be kept separate, because TapeOut only
solves the first:

  A. IMMUTABLE SCORING RULES — solved. The rubric is a circuit on a frozen
     processor (ownerless beacon, verified on chain). After tape-out nobody can
     change how a score is computed. This module models that with a digest that
     is fixed at freeze time.

  B. AUTHENTICITY / PROVENANCE OF EVALUATIONS — partly solved. A judge's
     evaluation is a signed, hash-committed record; a re-submission that changes
     a score is rejected. TapeJudge does not verify that the judge *is* who they
     claim: that needs a key registry the organiser controls (out of the MVP) or
     an external identity/attestation service.

  C. FAIRNESS / LEGITIMACY OF HUMAN JUDGMENT — NOT solved by TapeOut. The
     circuit proves "given these scores, this is the ranking". It does not prove
     the scores were fair, that the judges were independent, or that the chosen
     criteria were the right ones. Conflicts of interest, bias and collusion are
     human problems. TapeJudge's contribution is to make the *input* auditable
     and the *output* reproducible, so a dispute is about facts, not arithmetic.
     This limitation is stated in the product, not hidden.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field

from .engine import Competition, JudgeEvaluation
from .rubric import Rubric


class LifecycleError(RuntimeError):
    pass


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


@dataclass
class CompetitionRecord:
    """The persisted, auditable state of one competition.

    `rubric_digest` and `netlist_sha256` are recorded at freeze time and never
    recomputed from mutable state: a rubric edit after freeze is detectable by
    comparing digests.
    """
    rubric: Rubric
    rubric_digest: str
    netlist_hex: str
    netlist_sha256: str
    judges: tuple[str, ...] = ()
    entry_ids: tuple[str, ...] = ()
    entries: dict[str, str] = field(default_factory=dict)      # entry -> content hash
    evaluations: dict[str, dict] = field(default_factory=dict)  # "judge:entry" -> record
    frozen_at: str = ""
    settled: bool = False
    ranking: list = field(default_factory=list)


def freeze(rubric: Rubric, judges: list[str], entry_ids: list[str],
           entries: dict[str, str] | None = None) -> CompetitionRecord:
    """Publish and freeze a rubric. After this the rule cannot change."""
    comp = Competition(rubric=rubric, entry_ids=tuple(entry_ids))
    from .netlist import encode
    net = encode(comp._circuit.elements)
    return CompetitionRecord(
        rubric=rubric,
        rubric_digest=rubric.digest(),
        netlist_hex="0x" + net.hex(),
        netlist_sha256=hashlib.sha256(net).hexdigest(),
        judges=tuple(judges),
        entry_ids=tuple(entry_ids),
        entries=dict(entries or {}),
    )


def assert_unchanged(record: CompetitionRecord, candidate: Rubric) -> None:
    """Raise if a candidate rubric differs from the frozen one."""
    if candidate.digest() != record.rubric_digest:
        raise LifecycleError(
            "rubric was modified after it was frozen "
            f"(frozen {record.rubric_digest[:12]}…, candidate {candidate.digest()[:12]}…)")


def commit_evaluation(record: CompetitionRecord, ev: JudgeEvaluation,
                      secret: bytes) -> str:
    """Record a judge's evaluation with a signed commitment.

    The commitment is HMAC(secret, canonical evaluation). It binds the judge id,
    the entry, and every score. A later change to any field changes the
    commitment, so tampering is detectable. `secret` is the per-competition
    commitment key (a stand-in for the judge's signing key in the MVP).
    """
    if record.settled:
        raise LifecycleError("competition is settled; evaluations are closed")
    if ev.entry not in record.entry_ids:
        raise LifecycleError(f"unknown entry {ev.entry!r}")
    if ev.judge not in record.judges:
        raise LifecycleError(f"unknown judge {ev.judge!r}")
    payload = {"judge": ev.judge, "entry": ev.entry, "scores": ev.scores, "nonce": ev.nonce}
    commitment = hmac.new(secret, _canonical(payload), hashlib.sha256).hexdigest()
    key = f"{ev.judge}:{ev.entry}"
    if key in record.evaluations:
        prev = record.evaluations[key]
        if prev["commitment"] == commitment:
            return commitment  # idempotent re-submit of the identical evaluation
        raise LifecycleError(
            f"judge {ev.judge!r} already committed an evaluation for {ev.entry!r}; "
            "a submitted score cannot be changed")
    record.evaluations[key] = {"judge": ev.judge, "entry": ev.entry,
                               "scores": dict(ev.scores), "nonce": ev.nonce,
                               "commitment": commitment}
    return commitment


def verify_commitment(record: CompetitionRecord, ev: JudgeEvaluation, secret: bytes) -> bool:
    key = f"{ev.judge}:{ev.entry}"
    rec = record.evaluations.get(key)
    if not rec:
        return False
    payload = {"judge": ev.judge, "entry": ev.entry, "scores": ev.scores, "nonce": ev.nonce}
    return hmac.compare_digest(rec["commitment"], hmac.new(secret, _canonical(payload), hashlib.sha256).hexdigest())


def settle(record: CompetitionRecord) -> list:
    """Compute the final ranking from the committed evaluations."""
    if record.settled:
        raise LifecycleError("already settled")
    comp = Competition(rubric=record.rubric, entry_ids=record.entry_ids)
    for rec in record.evaluations.values():
        comp.submit(JudgeEvaluation(rec["judge"], rec["entry"], rec["scores"], rec["nonce"]))
    result = comp.rank()
    record.ranking = result["ranking"]
    record.settled = True
    return record.ranking


def verify_audit(record: CompetitionRecord) -> dict:
    """Independent check that the published record is internally consistent.

    Recomputes the rubric digest and the netlist hash from the record and
    compares them with the frozen values. Returns a report; `ok` is False on any
    inconsistency. This is what an entrant runs to prove the rule did not move.
    """
    from .netlist import encode
    from .rubric import compile_rubric
    circuit, _meta = compile_rubric(record.rubric)
    net = encode(circuit.elements)
    checks = {
        "rubric_digest_matches": record.rubric.digest() == record.rubric_digest,
        "netlist_sha256_matches": hashlib.sha256(net).hexdigest() == record.netlist_sha256,
        "netlist_hex_matches": ("0x" + net.hex()) == record.netlist_hex,
    }
    return {"ok": all(checks.values()), "checks": checks,
            "rubric_digest": record.rubric_digest,
            "netlist_sha256": record.netlist_sha256}
