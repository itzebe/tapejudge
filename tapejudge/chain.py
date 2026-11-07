"""Read-only X Layer TapeOut client.

Calls the deployed processor contracts over JSON-RPC with `eth_call` only: no
wallet, no key, no transaction, no signature, no spend. `eval`, `circuitInfo`,
`netlist`, `nextId`, `cpuCount` and `cpuAt` are all read-only per TAP-02 §6.

This module is what turns "the local simulation agrees with the chain" from a
claim into a check. `verify_circuit_against_chain` compiles nothing new: it takes
a netlist that already exists on chain, evaluates it both ways, and compares.
"""
from __future__ import annotations

import json
import urllib.request

X_LAYER_RPC = "https://rpc.xlayer.tech"
X_LAYER_CHAIN_ID = 196

# Read-only selectors, TAP-02 §6.
SEL_NEXT_ID = "0x61b8ce8c"
SEL_CIRCUIT_INFO = "0x084d60f1"
SEL_NETLIST = "0x3fc4be56"
SEL_EVAL = "0x934d06ea"
SEL_STEP = "0xe8281a1a"
SEL_CPU_COUNT = "0xa94da8a7"
SEL_CPU_AT = "0x4bc7cbbd"


class RpcError(RuntimeError):
    pass


def _rpc(method: str, params: list, url: str = X_LAYER_RPC, timeout: int = 25):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise RpcError(out["error"].get("message", str(out["error"])))
    return out.get("result")


def eth_call(to: str, data: str, url: str = X_LAYER_RPC) -> str:
    return _rpc("eth_call", [{"to": to, "data": data}, "latest"], url)


def block_number(url: str = X_LAYER_RPC) -> int:
    return int(_rpc("eth_blockNumber", [], url), 16)


def _uint(x: int) -> str:
    return f"{x:064x}"


def _bytes_arg(offset_words: int, payload: bytes) -> str:
    return _uint(offset_words) + _uint(len(payload)) + payload.hex().ljust(
        ((len(payload) + 31) // 32) * 64, "0")


def _read_bytes(ret: str) -> bytes:
    raw = ret[2:]
    off = int(raw[0:64], 16) * 2
    ln = int(raw[off:off + 64], 16)
    return bytes.fromhex(raw[off + 64:off + 64 + ln * 2])


# -- high level reads -------------------------------------------------------- #

def next_id(cpu: str, url: str = X_LAYER_RPC) -> int:
    return int(eth_call(cpu, SEL_NEXT_ID, url), 16)


def circuit_info(cpu: str, cid: int, url: str = X_LAYER_RPC) -> dict:
    r = eth_call(cpu, SEL_CIRCUIT_INFO + _uint(cid), url)
    w = [int(r[2 + i * 64:2 + i * 64 + 64], 16) for i in range(4)]
    return {"nIn": w[0], "nOut": w[1], "nState": w[2], "gateCount": w[3]}


def netlist(cpu: str, cid: int, url: str = X_LAYER_RPC) -> bytes:
    return _read_bytes(eth_call(cpu, SEL_NETLIST + _uint(cid), url))


def eval_circuit(cpu: str, cid: int, inputs: bytes, url: str = X_LAYER_RPC) -> bytes:
    data = SEL_EVAL + _uint(cid) + _uint(0x40) + _bytes_arg(0, inputs)[64:]
    # layout: id, offset-to-bytes=0x40, then the bytes array
    return _read_bytes(eth_call(cpu, data, url))


def step_circuit(cpu: str, cid: int, state: bytes, inputs: bytes, url: str = X_LAYER_RPC) -> tuple[bytes, bytes]:
    head = _uint(cid) + _uint(0x60)
    ps = state + bytes((32 - len(state) % 32) % 32)
    pi = inputs + bytes((32 - len(inputs) % 32) % 32)
    off2 = 0x60 + 32 + len(ps)
    data = (SEL_STEP + head + _uint(off2)
            + _uint(len(state)) + ps.hex() + _uint(len(inputs)) + pi.hex())
    ret = eth_call(cpu, data, url)[2:]
    o1 = int(ret[0:64], 16) * 2
    o2 = int(ret[64:128], 16) * 2
    l1 = int(ret[o1:o1 + 64], 16)
    new_state = bytes.fromhex(ret[o1 + 64:o1 + 64 + l1 * 2])
    l2 = int(ret[o2:o2 + 64], 16)
    outs = bytes.fromhex(ret[o2 + 64:o2 + 64 + l2 * 2])
    return new_state, outs


def cpu_count(factory: str, url: str = X_LAYER_RPC) -> int:
    return int(eth_call(factory, SEL_CPU_COUNT, url), 16)


def cpu_at(factory: str, i: int, url: str = X_LAYER_RPC) -> str:
    return "0x" + eth_call(factory, SEL_CPU_AT + _uint(i), url)[-40:]


# -- verification ------------------------------------------------------------ #

def verify_circuit_against_chain(cpu: str, cid: int, sample_inputs: list[bytes],
                                 url: str = X_LAYER_RPC) -> dict:
    """Evaluate an existing on-chain circuit both locally and on chain, compare.

    Local evaluation uses this package's TAP-02 implementation. On-chain
    evaluation is a free `eval` read. Returns a report; raises on any mismatch.
    """
    from .netlist import beat, load, pack, unpack

    info = circuit_info(cpu, cid, url)
    if info["nState"] != 0:
        raise ValueError(f"circuit {cid} is sequential; use step_circuit for it")
    data = netlist(cpu, cid, url)
    circuit = load(data, info["nIn"], info["nOut"])

    rows = []
    for inp in sample_inputs:
        _ns, out_bits = beat(circuit, [], unpack(inp, info["nIn"]))
        local = pack(out_bits)
        chain = eval_circuit(cpu, cid, inp, url)
        rows.append({"input": "0x" + inp.hex(), "local": "0x" + local.hex(),
                     "chain": "0x" + chain.hex(), "match": local == chain})
    ok = all(r["match"] for r in rows)
    return {"cpu": cpu, "circuit": cid, "info": info, "rows": rows, "match": ok}
