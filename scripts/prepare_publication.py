#!/usr/bin/env python3
"""Prepare the human-readable TapeOut publication package for a competition.

Read-only. This compiles nothing and sends nothing: it reads the frozen rubric
from the store, builds the exact unsigned ``tapeout(bytes,uint32,uint32)`` call,
reads the live chain facts it can (processor, next circuit id, TAPEOUT_FEE), and
prints everything a human needs to sign and broadcast it themselves — plus the
one command that confirms the result afterwards.

    python3 scripts/prepare_publication.py <db> <competition_id> [--json out.json]

Nothing here holds a key, signs, or broadcasts. Publishing remains the operator's
explicit action.
"""
from __future__ import annotations

import json
import sys

from tapejudge.store import Store
from tapejudge.xlayer import TAPEOUT_SELECTOR, XLayerClient, _encode_tapeout_args


def calldata_intrinsic_floor(calldata: bytes) -> int:
    """A lower bound on gas from calldata alone (EIP-2028 + the 21k base).

    Every byte costs 4 gas if zero and 16 if not; the transaction also pays the
    21000 base. This is a *floor* — execution (the transistor burn and circuit
    storage) costs more. It is printed so the operator is not surprised by a
    large-calldata transaction, never as a final estimate.
    """
    zeros = sum(1 for b in calldata if b == 0)
    nonzero = len(calldata) - zeros
    return 21000 + zeros * 4 + nonzero * 16


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    db_path, cid = argv[1], argv[2]
    out_json = None
    if "--json" in argv:
        out_json = argv[argv.index("--json") + 1]

    store = Store(db_path)
    comp = store.get_competition(cid)
    if not comp:
        print(f"no competition {cid!r} in {db_path}", file=sys.stderr)
        return 1
    if not comp.get("rubric_json"):
        print("competition has no rubric; nothing to publish", file=sys.stderr)
        return 1
    if comp.get("status") == "draft":
        print("competition is a draft; freeze the rubric first", file=sys.stderr)
        return 1

    netlist = bytes.fromhex(comp["netlist_hex"][2:])
    client = XLayerClient()
    prepared = client.prepare_tapeout(netlist, comp["n_in"], comp["n_out"],
                                      n_gates=comp.get("gates"))
    data = TAPEOUT_SELECTOR + _encode_tapeout_args(netlist, comp["n_in"], comp["n_out"])
    raw = bytes.fromhex(data[2:])
    floor = calldata_intrinsic_floor(raw)

    package = {
        "competition": {"id": comp["id"], "name": comp["name"],
                        "rubric_digest": comp["rubric_digest"],
                        "netlist_sha256": comp["netlist_sha256"],
                        "gates": comp.get("gates")},
        "transaction": {
            "to": prepared.get("processor"),
            "chainId": prepared.get("chain_id"),
            "value_wei": prepared.get("fee_wei"),
            "value_okb": (prepared["fee_wei"] / 1e18
                          if prepared.get("fee_known") else None),
            "data": data,
            "data_bytes": len(raw),
            "selector": TAPEOUT_SELECTOR,
            "gas_floor": floor,
        },
        "expected_circuit_id": prepared.get("circuit_id"),
        "transistors_to_burn": comp.get("gates"),
        "chain_readable": prepared.get("mode") != "local_only",
        "prepared_note": prepared.get("note"),
    }

    print("=" * 72)
    print("TapeJudge — TapeOut publication package (unsigned; nothing is sent)")
    print("=" * 72)
    print(f"Competition      : {comp['name']}  ({comp['id']})")
    print(f"Rubric digest    : {comp['rubric_digest']}")
    print(f"Netlist SHA-256  : {comp['netlist_sha256']}")
    print(f"Circuit size     : {comp.get('gates')} gates, "
          f"{comp['n_in']} in, {comp['n_out']} out")
    print("-" * 72)
    print("The transaction your wallet must sign:")
    print(f"  to        : {package['transaction']['to']}")
    print(f"  chainId   : {package['transaction']['chainId']}")
    print(f"  value     : {package['transaction']['value_wei']} wei"
          + (f"  ({package['transaction']['value_okb']} OKB)"
             if package["transaction"]["value_okb"] is not None else "  (fee unread)"))
    print(f"  selector  : {package['transaction']['selector']}  tapeout(bytes,uint32,uint32)")
    print(f"  data      : {package['transaction']['data_bytes']} bytes "
          f"(0x7bd3ac1d…)")
    print(f"  gas floor : >= {package['transaction']['gas_floor']} (calldata only; "
          "the burn and storage cost more)")
    print("-" * 72)
    print(f"Expected new circuit id : {package['expected_circuit_id']}")
    print(f"Transistors to burn     : {package['transistors_to_burn']} "
          "(from the signing wallet's balance)")
    print("-" * 72)
    print("Steps (you, the operator, do these — the app cannot):")
    print("  1. Fund the signing wallet with the TAPEOUT_FEE in OKB and at least")
    print(f"     {package['transistors_to_burn']} transistor tokens, plus gas.")
    print("  2. Send exactly this call (e.g. `cast send`), with msg.value = the fee:")
    print(f"       cast send {package['transaction']['to']} \\")
    print("         'tapeout(bytes,uint32,uint32)' <netlist-hex> "
          f"{comp['n_in']} {comp['n_out']} \\")
    print(f"         --value {package['transaction']['value_wei']} "
          f"--chain 196 --rpc-url https://rpc.xlayer.tech")
    print("  3. Copy the transaction hash.")
    print("  4. Confirm it in the app (Publish → 'Confirm publication'), which")
    print("     reads the receipt and the circuit back and marks it published only")
    print("     if the on-chain netlist matches the frozen one.")
    print("-" * 72)
    print("Confirm by hand (read-only):")
    print("       cast receipt <txhash> --rpc-url https://rpc.xlayer.tech")
    print(f"       cast call {package['transaction']['to']} \\")
    print(f"         'netlist(uint256)(bytes)' {package['expected_circuit_id']} "
          "--rpc-url https://rpc.xlayer.tech")
    print("     The returned bytes must hash to the netlist SHA-256 above.")
    print("=" * 72)

    if out_json:
        with open(out_json, "w") as fh:
            json.dump(package, fh, indent=2)
        print(f"wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
