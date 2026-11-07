"""TapeJudge circuit layer.

A faithful, dependency-free implementation of the TapeOut circuit model as
specified in TAP-02 (NAND / LATCH / REF records, LSB-first bit packing, one beat
of evaluation). This is the *same* model the deployed X Layer processor contracts
evaluate (see ../evidence/CHAIN_VERIFICATION.md), so a circuit compiled here can
be checked against the chain and vice versa.

Nothing in this module talks to a network. It is pure and deterministic, which is
exactly what makes it usable as a verifier.
"""
from __future__ import annotations

from dataclasses import dataclass, field

NAND, LATCH, REF = 0x00, 0x01, 0x02
MAX_SIGNALS = 1 << 24
MAX_PINS = 1 << 16
MAX_GATES = (1 << 32) - 1


class IllFormed(ValueError):
    """A netlist that TAP-02 §3 rejects. The chain would reject it too."""


@dataclass
class Element:
    op: int
    ins: list[int]
    cpu: bytes = b""
    cid: int = 0
    n_out: int = 1
    first_out: int = 0


@dataclass
class Circuit:
    n_in: int
    n_out: int
    elements: list[Element]
    n_signals: int = 0
    subs: dict = field(default_factory=dict)
    n_state: int = 0
    n_gates: int = 0
    state_base: list[int] = field(default_factory=list)


def encode(elements: list[Element]) -> bytes:
    out = bytearray()
    for e in elements:
        out.append(e.op)
        if e.op == REF:
            out += e.cpu + e.cid.to_bytes(8, "big") + bytes([len(e.ins), e.n_out])
        for s in e.ins:
            out += s.to_bytes(3, "big")
    return bytes(out)


def decode(data: bytes, n_in: int) -> list[Element]:
    p, nxt, els = 0, 2 + n_in, []

    def take(k: int) -> bytes:
        nonlocal p
        if p + k > len(data):
            raise IllFormed(f"truncated record at byte {p}")
        b = data[p:p + k]
        p += k
        return b

    while p < len(data):
        op = take(1)[0]
        if op == NAND:
            e = Element(NAND, [int.from_bytes(take(3), "big"), int.from_bytes(take(3), "big")])
        elif op == LATCH:
            e = Element(LATCH, [int.from_bytes(take(3), "big")])
        elif op == REF:
            cpu = take(20)
            cid = int.from_bytes(take(8), "big")
            ni, no = take(1)[0], take(1)[0]
            e = Element(REF, [int.from_bytes(take(3), "big") for _ in range(ni)], cpu, cid, no)
        else:
            raise IllFormed(f"unknown opcode 0x{op:02x} at byte {p - 1}")
        e.first_out = nxt
        nxt += e.n_out
        els.append(e)
    return els


def load(data: bytes, n_in: int, n_out: int, resolve=None) -> Circuit:
    """Decode and check well-formedness (TAP-02 §3). resolve(cpu, cid) -> Circuit."""
    els = decode(data, n_in)
    n_sig = 2 + n_in + sum(e.n_out for e in els)
    if n_sig > MAX_SIGNALS:
        raise IllFormed("more than 2^24 signals")
    if not (0 <= n_in <= MAX_PINS and 1 <= n_out <= MAX_PINS):
        raise IllFormed("nIn/nOut out of range")
    if n_sig - 2 - n_in < n_out:
        raise IllFormed("too few signals for outputs")
    c = Circuit(n_in, n_out, els, n_sig)
    for e in els:
        for s in e.ins:
            limit = n_sig if e.op == LATCH else e.first_out
            if s >= limit:
                raise IllFormed(f"signal {s} referenced by element at {e.first_out} is not available")
        c.state_base.append(c.n_state)
        if e.op == LATCH:
            c.n_state += 1
        elif e.op == REF:
            if resolve is None:
                raise IllFormed("REF without a resolver")
            sub = resolve(e.cpu, e.cid)
            if sub is None:
                raise IllFormed("REF target is not a circuit of a registered processor")
            if (sub.n_in, sub.n_out) != (len(e.ins), e.n_out):
                raise IllFormed("REF arity does not match the target circuit")
            c.subs[(e.cpu, e.cid)] = sub
            c.n_state += sub.n_state
            c.n_gates += sub.n_gates
    c.n_gates += sum(1 for e in els if e.op != REF)
    if c.n_state > MAX_SIGNALS or c.n_gates > MAX_GATES:
        raise IllFormed("size overflow")
    return c


def beat(c: Circuit, state: list[int], inputs: list[int]) -> tuple[list[int], list[int]]:
    """One beat (TAP-02 §4). Returns (new_state, outputs)."""
    if len(state) != c.n_state or len(inputs) != c.n_in:
        raise ValueError("state/input length does not match the circuit")
    a = [0, 1] + list(inputs)
    new_state = list(state)
    for e, base in zip(c.elements, c.state_base):
        if e.op == NAND:
            a.append(1 - (a[e.ins[0]] & a[e.ins[1]]))
        elif e.op == LATCH:
            a.append(state[base])
        else:
            sub = c.subs[(e.cpu, e.cid)]
            ns, outs = beat(sub, state[base:base + sub.n_state], [a[s] for s in e.ins])
            new_state[base:base + sub.n_state] = ns
            a.extend(outs)
    for e, base in zip(c.elements, c.state_base):
        if e.op == LATCH:
            new_state[base] = a[e.ins[0]]
    return new_state, a[len(a) - c.n_out:]


def pack(bits: list[int]) -> bytes:
    out = bytearray((len(bits) + 7) // 8)
    for i, v in enumerate(bits):
        if v:
            out[i >> 3] |= 1 << (i & 7)
    return bytes(out)


def unpack(data: bytes, n: int) -> list[int]:
    """As the deployed evaluator reads inputs/state: missing bytes read as 0."""
    return [(data[i >> 3] >> (i & 7)) & 1 if (i >> 3) < len(data) else 0 for i in range(n)]


# --------------------------------------------------------------------------- #
# Combinational building blocks (all built from NAND only -> no state, so the
# result is a pure function of the inputs and `eval()` is enough).
# --------------------------------------------------------------------------- #

class NetlistBuilder:
    """Accumulates NAND gates and tracks signal indices per TAP-02 §2.

    Signals: 0 = const 0, 1 = const 1, 2..2+n_in-1 = inputs, then one signal per
    element in record order.
    """

    def __init__(self, n_in: int):
        if not (0 <= n_in <= MAX_PINS):
            raise IllFormed("nIn out of range")
        self.n_in = n_in
        self.elements: list[Element] = []
        self.next_signal = 2 + n_in

    def _emit(self, a: int, b: int) -> int:
        e = Element(NAND, [a, b], first_out=self.next_signal)
        self.elements.append(e)
        self.next_signal += 1
        return e.first_out

    @property
    def zero(self) -> int:
        return 0

    @property
    def one(self) -> int:
        return 1

    def input(self, i: int) -> int:
        if not (0 <= i < self.n_in):
            raise IllFormed("input index out of range")
        return 2 + i

    def nand(self, a: int, b: int) -> int:
        return self._emit(a, b)

    def not_(self, a: int) -> int:
        return self._emit(a, a)

    def and_(self, a: int, b: int) -> int:
        return self.not_(self._emit(a, b))

    def or_(self, a: int, b: int) -> int:
        return self._emit(self.not_(a), self.not_(b))

    def xor(self, a: int, b: int) -> int:
        t = self._emit(a, b)
        return self._emit(self._emit(a, t), self._emit(b, t))

    def mux(self, sel: int, a: int, b: int) -> int:
        """sel ? a : b  (a when sel=1)."""
        return self.or_(self.and_(sel, a), self.and_(self.not_(sel), b))

    def and_all(self, sigs: list[int]) -> int:
        if not sigs:
            return self.one
        acc = sigs[0]
        for s in sigs[1:]:
            acc = self.and_(acc, s)
        return acc

    def or_all(self, sigs: list[int]) -> int:
        if not sigs:
            return self.zero
        acc = sigs[0]
        for s in sigs[1:]:
            acc = self.or_(acc, s)
        return acc

    def build(self, n_out: int) -> Circuit:
        if not (1 <= n_out <= MAX_PINS):
            raise IllFormed("nOut out of range")
        if self.next_signal - 2 - self.n_in < n_out:
            raise IllFormed("too few signals for outputs")
        data = encode(self.elements)
        return load(data, self.n_in, n_out)

    def to_netlist(self, n_out: int) -> tuple[bytes, int]:
        self.build(n_out)  # validates
        return encode(self.elements), n_out


# --------------------------------------------------------------------------- #
# Bit-vector helpers: a fixed-width unsigned integer split into signals.
# --------------------------------------------------------------------------- #

def encode_uint(b: NetlistBuilder, value: int, width: int) -> list[int]:
    """Return `width` signals holding the bits of `value` (LSB first, mod 2^width)."""
    return [b.one if (value >> k) & 1 else b.zero for k in range(width)]


def decode_uint(bits: list[int]) -> int:
    return sum(1 << k for k, v in enumerate(bits) if v)


def add(b: NetlistBuilder, x: list[int], y: list[int], carry_in: int | None = None) -> tuple[list[int], int]:
    """Ripple-carry adder. Returns (sum bits, carry-out signal)."""
    n = max(len(x), len(y))
    x = x + [b.zero] * (n - len(x))
    y = y + [b.zero] * (n - len(y))
    carry = carry_in if carry_in is not None else b.zero
    out = []
    for i in range(n):
        s = b.xor(b.xor(x[i], y[i]), carry)
        c1 = b.and_(x[i], y[i])
        c2 = b.and_(carry, b.xor(x[i], y[i]))
        carry = b.or_(c1, c2)
        out.append(s)
    return out, carry


def mul_const(b: NetlistBuilder, x: list[int], k: int, out_width: int) -> list[int]:
    """Multiply a bit-vector by a small integer constant k using shift-and-add.

    Cost is ~popcount(k) ripple-carry adders, which is why rubrics keep weights
    small and the score width bounded.
    """
    if k < 0:
        raise ValueError("negative weights are not supported in the combinational MVP")
    acc = [b.zero] * out_width
    for bit in range(k.bit_length()):
        if (k >> bit) & 1:
            shifted = ([b.zero] * bit + x)[:out_width]
            shifted = shifted + [b.zero] * (out_width - len(shifted))
            acc, _ = add(b, acc, shifted)
    return acc


def gt(b: NetlistBuilder, x: list[int], y: list[int]) -> int:
    """x > y for equal-width unsigned bit vectors.

    MSB-down: the highest bit at which x and y differ decides. `eq_prefix`
    tracks whether every higher bit has been equal, so a lower bit can only set
    the result while no higher bit has decided yet.
    """
    n = max(len(x), len(y))
    x = x + [b.zero] * (n - len(x))
    y = y + [b.zero] * (n - len(y))
    acc = b.zero
    eq_prefix = b.one
    for i in range(n - 1, -1, -1):
        hi = b.and_(x[i], b.not_(y[i]))          # x bit 1, y bit 0
        lo = b.and_(b.not_(x[i]), y[i])          # x bit 0, y bit 1
        eq = b.not_(b.or_(hi, lo))               # bits equal
        new_acc = b.or_(acc, b.and_(eq_prefix, hi))
        eq_prefix = b.and_(eq_prefix, eq)
        acc = new_acc                            # emitted last, so it is the result
    return acc
