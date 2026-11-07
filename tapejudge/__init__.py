"""TapeJudge — verifiable competition scoring on TapeOut / X Layer.

The scoring core (``netlist``, ``rubric``, ``engine``, ``verifier``, ``lifecycle``,
``chain``) is the verified Mission 3 implementation. The application layer
(``store``, ``service``, ``xlayer``, ``web``) builds the product on top of it.
"""
from . import chain, lifecycle, xlayer
from .engine import Competition, JudgeEvaluation, ScoringError
from .netlist import IllFormed, NetlistBuilder, beat, decode, encode, load, pack, unpack
from .rubric import Criterion, Rubric, compile_rubric, decode_outputs
from .service import ConflictError, TapeJudgeService, ValidationError
from .store import Store
from .verifier import reproduce_entry, verify_ranking

__all__ = [
    "Competition",
    "ConflictError",
    "Criterion",
    "IllFormed",
    "JudgeEvaluation",
    "NetlistBuilder",
    "Rubric",
    "ScoringError",
    "Store",
    "TapeJudgeService",
    "ValidationError",
    "beat",
    "chain",
    "compile_rubric",
    "decode",
    "decode_outputs",
    "encode",
    "lifecycle",
    "load",
    "pack",
    "reproduce_entry",
    "unpack",
    "verify_ranking",
    "xlayer",
]
