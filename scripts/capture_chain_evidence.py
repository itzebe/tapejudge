"""Capture read-only X Layer evidence for the integration status document.

Everything here is ``eth_call``: no wallet, no key, no transaction, no spend. The
output is written to ``evidence/chain_evidence.json`` and printed, so the numbers
in TAPEJUDGE_XLAYER_INTEGRATION_STATUS.md are reproducible.

    python scripts/capture_chain_evidence.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge import chain
from tapejudge.netlist import encode
from tapejudge.rubric import Criterion, Rubric, compile_rubric
from tapejudge.xlayer import DEFAULT_FACTORY, XLayerClient, selector

BEACON = "0xf70d1ed4f62cf3780157b0b421b7e2f45bd0991c"


def main() -> int:
    out: dict = {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "rpc": chain.X_LAYER_RPC, "chain_id": chain.X_LAYER_CHAIN_ID}
    client = XLayerClient()
    try:
        out["block_number"] = client.block_number()
    except Exception as e:
        print(f"RPC unreachable: {e}")
        return 2

    out["factory"] = DEFAULT_FACTORY
    out["cpu_count"] = client.cpu_count()
    out["processor_0"] = client.processor_at(0)
    out["processor_0_next_id"] = client.next_id(out["processor_0"])

    # The beacon's owner is what makes the circuit layer frozen.
    out["beacon"] = BEACON
    out["beacon_owner"] = "0x" + chain.eth_call(BEACON, selector("owner()"))[-40:]
    out["beacon_implementation"] = "0x" + chain.eth_call(BEACON, selector("implementation()"))[-40:]
    out["circuit_layer_frozen"] = out["beacon_owner"].lower() == "0x" + "0" * 40

    # A representative rubric compiled exactly as the app compiles it.
    rubric = Rubric("Evidence Round",
                    (Criterion("demo", 3, 10), Criterion("tests", 2, 10),
                     Criterion("review", 1, 5)), judges=3,
                    tie_break=("tests", "demo", "review"))
    circuit, meta = compile_rubric(rubric)
    net = encode(circuit.elements)
    out["sample_rubric"] = {
        "digest": rubric.digest(),
        "n_in": meta["n_in"], "n_out": meta["n_out"], "gates": circuit.n_gates,
        "netlist_bytes": len(net),
        "netlist_sha256": hashlib.sha256(net).hexdigest(),
    }

    # The publication preparation (never a send).
    prepared = client.prepare_tapeout(net, meta["n_in"], meta["n_out"])
    out["prepare_tapeout"] = {k: v for k, v in prepared.items() if k != "tx"}
    if prepared.get("tx"):
        out["prepare_tapeout"]["tx_calldata_prefix"] = prepared["tx"]["data"][:74] + "…"
        out["prepare_tapeout"]["tx_unsigned"] = prepared["tx"]["unsigned"]

    # An existing on-chain circuit, evaluated locally and on chain (a free read).
    try:
        rep = client.verify_circuit_against_chain(
            1, [bytes([i]) for i in range(4)], out["processor_0"])
        out["circuit_1_cross_check"] = {"match": rep["match"], "info": rep["info"],
                                        "rows": rep["rows"]}
    except Exception as e:
        out["circuit_1_cross_check"] = {"error": str(e)}

    dest = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "evidence", "chain_evidence.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    print(json.dumps(out, indent=2, sort_keys=True))
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
