"""TapeJudge <-> X Layer TapeOut integration.

This module is the *only* place that talks to X Layer. It is deliberately split
into two halves:

* **Reads** — ``eval_circuit``, ``circuit_info``, ``netlist``, ``next_id``,
  ``cpu_count``, ``cpu_at``, ``verify_circuit_against_chain``. These are
  ``eth_call`` only: no wallet, no key, no transaction. They are WORKING AND
  VERIFIED against the live X Layer contracts (see evidence/CHAIN_VERIFICATION.md).
  They are what lets the public proof page compare a locally computed result with
  the chain's own ``eval``.

* **Writes** — ``prepare_tapeout``. Publishing a circuit is a *funded, signed*
  transaction (TAP-02 §6: ``tapeout(bytes,uint32,uint32)`` = selector
  ``0x7bd3ac1d``, requires ``msg.value`` to equal the implementation's
  ``TAPEOUT_FEE`` and burns the caller's gate count). That is an authorisation
  boundary this mission must not cross. So the write half only ever *prepares*
  the call: it compiles the netlist, reads what it can from the chain, and
  returns the exact unsigned transaction the organiser's wallet must sign. It
  never sends anything, and it never reports a local circuit as on-chain.

``chain.py`` (the Mission 3 read client) is reused unchanged for the read half;
this module wraps it and adds the preparation half, so there is exactly one
implementation of each selector.
"""
from __future__ import annotations

import hashlib

from . import chain as _chain

X_LAYER_CHAIN_ID = _chain.X_LAYER_CHAIN_ID

# From the revalidated Mission 3/4 evidence (read live; see the integration
# status document). The factory is a default the caller may override;
# ``prepare_tapeout`` re-reads ``cpuCount``/``cpuAt`` before using it.
DEFAULT_FACTORY = "0x1f09DAeFA827f02CBb40967cc91b259763760761"
DEFAULT_PROCESSOR = "0x839bdd6fa7a66416a609a735e11de5411b98574e"

# Read-only selectors (TAP-02 §6). One list, no duplicates elsewhere.
SEL = {
    "nextId": "0x61b8ce8c",
    "circuitInfo": "0x084d60f1",
    "netlist": "0x3fc4be56",
    "eval": "0x934d06ea",
    "step": "0xe8281a1a",
    "beat": "0x1fc0021d",
    "cpuCount": "0xa94da8a7",
    "cpuAt": "0x4bc7cbbd",
}
# tapeout(bytes,uint32,uint32) -> 0x7bd3ac1d (TAP-02 §6; computed, not trusted)
TAPEOUT_SELECTOR = "0x7bd3ac1d"


def keccak256(msg: bytes) -> bytes:
    """Keccak-256, implemented inline so the module has no dependencies.

    Kept small and self-contained so the selector below is reproducible by anyone
    reading this file. Verified against the canonical vectors
    (``keccak256("")`` = c5d2460186f7…, ``keccak256("abc")`` = 4e03657aea45…).
    """
    RC = [
        0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
        0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
        0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
        0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
        0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
        0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
    ]
    R = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
         [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
    MASK = (1 << 64) - 1

    def rol(x, n):
        return ((x << n) | (x >> (64 - n))) & MASK

    rate = 136
    data = bytearray(msg)
    data.append(0x01)
    while len(data) % rate != 0:
        data.append(0x00)
    data[-1] ^= 0x80
    S = [[0] * 5 for _ in range(5)]
    for off in range(0, len(data), rate):
        block = data[off:off + rate]
        for i in range(rate // 8):
            S[i % 5][i // 5] ^= int.from_bytes(block[i * 8:i * 8 + 8], "little")
        for rnd in range(24):
            C = [S[x][0] ^ S[x][1] ^ S[x][2] ^ S[x][3] ^ S[x][4] for x in range(5)]
            D = [C[(x - 1) % 5] ^ rol(C[(x + 1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    S[x][y] ^= D[x]
            B = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    B[y][(2 * x + 3 * y) % 5] = rol(S[x][y], R[x][y])
            for x in range(5):
                for y in range(5):
                    S[x][y] = B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y])
            S[0][0] ^= RC[rnd]
    out = bytearray()
    for i in range(4):
        out += S[i % 5][i // 5].to_bytes(8, "little")
    return bytes(out)


def selector(signature: str) -> str:
    return "0x" + keccak256(signature.encode()).hex()[:8]


# Self-check at import: the computed selector must equal the TAP-02 constant.
assert selector("tapeout(bytes,uint32,uint32)") == TAPEOUT_SELECTOR, \
    "tapeout selector mismatch — keccak implementation is wrong"


class TapeoutBlocked(RuntimeError):
    """Raised when a real publication is requested without authorisation."""


class XLayerClient:
    """Read-only client plus a *preparation-only* tape-out builder."""

    def __init__(self, rpc_url: str = _chain.X_LAYER_RPC,
                 factory: str = DEFAULT_FACTORY,
                 processor: str = DEFAULT_PROCESSOR,
                 allow_send: bool = False):
        self.rpc_url = rpc_url
        self.factory = factory
        self.processor = processor
        # ``allow_send`` makes the boundary explicit and greppable. It is False
        # everywhere in the app; a real publication is a separate, approved change
        # (see TAPEJUDGE_REMAINING_WORK.md).
        self.allow_send = allow_send

    # -- reads (verified) --------------------------------------------------- #
    def block_number(self) -> int:
        return _chain.block_number(self.rpc_url)

    def cpu_count(self) -> int:
        return _chain.cpu_count(self.factory, self.rpc_url)

    def processor_at(self, i: int) -> str:
        return _chain.cpu_at(self.factory, i, self.rpc_url)

    def next_id(self, processor: str | None = None) -> int:
        return _chain.next_id(processor or self.processor, self.rpc_url)

    def circuit_info(self, cid: int, processor: str | None = None) -> dict:
        return _chain.circuit_info(processor or self.processor, cid, self.rpc_url)

    def netlist(self, cid: int, processor: str | None = None) -> bytes:
        return _chain.netlist(processor or self.processor, cid, self.rpc_url)

    def eval_circuit(self, processor: str, cid: int, inputs: bytes) -> bytes:
        return _chain.eval_circuit(processor, cid, inputs, self.rpc_url)

    def verify_circuit_against_chain(self, cid: int, samples, processor: str | None = None) -> dict:
        return _chain.verify_circuit_against_chain(
            processor or self.processor, cid, list(samples), self.rpc_url)

    def verify_publication(self, circuit_id: int, expected_netlist: bytes,
                           processor: str | None = None) -> dict:
        """Confirm a real on-chain circuit matches the frozen netlist.

        This is the read half of publication: given the circuit id the app
        recorded when it prepared the tapeout, read the circuit back from X Layer
        and compare it, byte for byte, with the frozen netlist. It is the only
        thing that can move a competition from *prepared* to *published* — a
        prepared call is not a publication, and the app must never claim one is.

        Returns an honest verdict, never an exception for an expected state:

        * ``match`` — the circuit exists and its netlist equals the frozen one;
        * ``mismatch`` — a circuit exists at that id but the bytes differ;
        * ``not_found`` — no circuit exists at that id (the tapeout never landed);
        * ``unavailable`` — the RPC could not be read, so the state is *unknown*.
        """
        import hashlib
        proc = processor or self.processor
        out = {
            "processor": proc,
            "circuit_id": circuit_id,
            "expected_netlist_sha256": hashlib.sha256(expected_netlist).hexdigest(),
            "chain_netlist_sha256": None,
            "chain_info": None,
            "verdict": "unavailable",
            "reason": "",
        }
        try:
            info = self.circuit_info(circuit_id, proc)
            on_chain = self.netlist(circuit_id, proc)
        except Exception as e:
            msg = str(e)
            # "no circuit" is the contract's own answer for an id that was never
            # taped out — a definitive "not found", not an outage.
            if "no circuit" in msg.lower():
                out["verdict"] = "not_found"
                out["reason"] = (
                    f"No circuit #{circuit_id} exists on {proc}; the tapeout has "
                    "not been mined, or it was recorded with the wrong id.")
            else:
                out["verdict"] = "unavailable"
                out["reason"] = f"X Layer RPC could not be read: {msg}"
            return out
        out["chain_info"] = info
        out["chain_netlist_sha256"] = hashlib.sha256(on_chain).hexdigest()
        if on_chain == expected_netlist:
            out["verdict"] = "match"
            out["reason"] = (
                f"Circuit #{circuit_id} on {proc} carries the frozen netlist "
                f"(sha256 {out['chain_netlist_sha256']}).")
        else:
            out["verdict"] = "mismatch"
            out["reason"] = (
                f"Circuit #{circuit_id} exists but its netlist is "
                f"{out['chain_netlist_sha256']}, not the frozen "
                f"{out['expected_netlist_sha256']}.")
        return out

    def is_reachable(self) -> bool:
        """Whether the X Layer RPC answers a cheap read right now.

        A reachability probe sits on the request path (the health endpoint and
        the competition list), so it uses a short timeout: a serverless function
        must not spend its whole budget waiting on a stalled RPC. An unreachable
        node is reported honestly as unreachable, never as a stalled request.
        """
        try:
            _chain.block_number(self.rpc_url, timeout=4)
            return True
        except Exception:
            return False

    def get_receipt(self, tx_hash: str) -> dict | None:
        """The receipt for a transaction hash, or ``None`` while it is unmined."""
        return _chain.get_transaction_receipt(tx_hash, self.rpc_url)

    # -- publication (preparation only) ------------------------------------ #
    def prepare_tapeout(self, netlist: bytes, n_in: int, n_out: int,
                        n_gates: int | None = None) -> dict:
        """Build the exact ``tapeout`` call for a wallet to sign. Send nothing.

        Returns a description the app records verbatim. When the RPC is
        unreachable it degrades to ``local_only`` with the reason — it never
        fabricates a chain id, a processor, a circuit id or a fee.

        ``n_gates`` is the top-level NAND+LATCH count of the compiled circuit.
        ``tapeout`` burns exactly that many transistor tokens from the caller
        (TAP-02 §6), so it is reported as a hard requirement next to the fee.
        """
        result = {
            "mode": "local_only",
            "chain_id": X_LAYER_CHAIN_ID,
            "processor": None,
            "circuit_id": None,
            "tx": None,
            "fee_wei": None,
            "fee_known": False,
            "n_gates": n_gates,
            "requirements": self._publication_requirements(n_gates),
            "netlist_bytes": len(netlist),
            "netlist_sha256": hashlib.sha256(netlist).hexdigest(),
            "note": "",
            "blocked_reason": None,
        }
        try:
            processors = self.cpu_count()
            processor = self.processor_at(0) if processors else self.processor
            result["processor"] = processor
            result["circuit_id"] = self.next_id(processor)
            result["fee_wei"], result["fee_known"] = self._read_tapeout_fee(processor)
        except Exception as e:
            result["note"] = (
                "X Layer RPC is unreachable, so the publication state cannot be "
                f"confirmed on chain: {e}")
            result["blocked_reason"] = "rpc_unreachable"
            return result

        result["requirements"] = self._publication_requirements(n_gates, result["fee_wei"])
        if not self.allow_send:
            result["mode"] = "onchain_pending_signature"
            result["note"] = (
                "The frozen circuit is compiled and the tapeout call is prepared. "
                "Publishing it is a funded, signed transaction; the organiser's "
                "wallet must sign it. Nothing has been sent.")
            result["tx"] = self._build_tapeout_tx(netlist, n_in, n_out, result)
        return result

    def _publication_requirements(self, n_gates: int | None, fee_wei: int | None = None) -> dict:
        """What a real ``tapeout`` needs, read from the chain where possible.

        TAP-02 §6: ``tapeout`` requires ``msg.value`` to equal the processor's
        ``TAPEOUT_FEE`` **and** burns the caller's top-level NAND+LATCH count in
        transistor tokens. Both are hard requirements; the app states them so an
        organiser knows what the wallet needs before signing.
        """
        fee_okb = None
        if isinstance(fee_wei, int):
            fee_okb = fee_wei / 1e18
        return {
            "fee_wei": fee_wei,
            "fee_okb": fee_okb,
            "fee_known": isinstance(fee_wei, int),
            "transistors_to_burn": n_gates,
            "native_coin": "OKB",
            "notes": [
                "msg.value must equal the processor's TAPEOUT_FEE exactly.",
                "The call burns the circuit's top-level gate count in transistor "
                "tokens held by the signing wallet (REF'd gates are not burned again).",
            ],
        }

    def simulate_tapeout(self, netlist: bytes, n_in: int, n_out: int,
                         from_address: str) -> dict:
        """Read-only ``eth_call`` of the real ``tapeout`` (sends nothing).

        It uses a state override to give ``from_address`` a large native balance
        so the *fee* check passes, which lets the call reach the §3 well-formedness
        checks and then the transistor burn. The returned result is the honest
        verdict of the live contract for this exact calldata:

        * the netlist is rejected with the §3 reason → ``ill_formed``;
        * it reaches the burn and reverts for lack of transistors →
          ``needs_transistors`` (the netlist is well-formed and the only missing
          requirement is the transistor balance);
        * it succeeds → ``would_tapeout`` (impossible from a tokenless address, but
          reported if the contract ever allows it).

        This is the closest a keyless app can come to proving a real tapeout: it
        exercises the deployed processor's own validation without spending
        anything.
        """
        from .chain import _rpc  # reuse the single RPC helper
        data = TAPEOUT_SELECTOR + _encode_tapeout_args(netlist, n_in, n_out)
        # Fund the simulated caller so the fee check passes; the transistor burn
        # is left to the contract, which is exactly the requirement we want to see.
        overrides = {from_address: {"balance": hex(10 ** 20)}}
        call = {"from": from_address, "to": self.processor, "data": data,
                "value": hex(self._read_tapeout_fee(self.processor)[0] or 0)}
        out = {"to": self.processor, "from": from_address,
               "value": call["value"], "calldata_bytes": len(data) // 2}
        try:
            res = _rpc("eth_call", [call, "latest", overrides], self.rpc_url)
        except Exception as e:
            msg = getattr(e, "args", [str(e)])[0]
            out["error"] = str(msg)
            if "tapeout fee" in str(msg):
                out["verdict"] = "fee_mismatch"
            elif "transistor" in str(msg).lower() or str(msg) == "execution reverted":
                out["verdict"] = "needs_transistors"
            else:
                out["verdict"] = "ill_formed"
            return out
        out["verdict"] = "would_tapeout"
        out["result"] = res
        return out

    def _build_tapeout_tx(self, netlist: bytes, n_in: int, n_out: int, info: dict) -> dict:
        """The exact unsigned transaction for ``tapeout(bytes,uint32,uint32)``."""
        # TAPEOUT_SELECTOR already carries the 0x prefix.
        data = TAPEOUT_SELECTOR + _encode_tapeout_args(netlist, n_in, n_out)
        return {
            "to": info["processor"],
            "chainId": X_LAYER_CHAIN_ID,
            "value": str(info.get("fee_wei")) if info.get("fee_known") else None,
            "data": data,
            "gas": None,
            "from": None,  # the signer fills this in
            "unsigned": True,
        }

    def _read_tapeout_fee(self, processor: str) -> tuple[int | None, bool]:
        """Read the public ``TAPEOUT_FEE()`` getter, if the processor has one.

        TAP-02 §6 documents no fee getter and states the fee is part of the
        implementation. The deployed X Layer processor *does* answer
        ``TAPEOUT_FEE()`` (read here, read-only); when it does not, the fee is
        **unknown** — never 0 — because the app must not print a number it did
        not read.
        """
        for sig in ("TAPEOUT_FEE()", "tapeoutFee()"):
            try:
                ret = _chain.eth_call(processor, selector(sig), self.rpc_url)
                # Accept only a full 32-byte word: a short or malformed response
                # must not be read as a fee.
                if len(ret) == 66:
                    return int(ret, 16), True
            except Exception:
                continue
        return None, False


def _encode_tapeout_args(netlist: bytes, n_in: int, n_out: int) -> str:
    """ABI-encode (bytes, uint32, uint32) — head(offset, nIn, nOut) + tail(bytes)."""

    def u(x: int) -> str:
        return f"{x:064x}"

    padded = netlist.hex().ljust(((len(netlist) + 31) // 32) * 64, "0")
    return u(0x60) + u(n_in) + u(n_out) + u(len(netlist)) + padded
