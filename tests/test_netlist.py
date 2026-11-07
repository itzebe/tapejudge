"""TAP-02 conformance tests for the netlist layer.

Covers: record round-trip, well-formedness, the official TAP-02 test vectors
(valid + ill-formed + packing edge cases), and the primitive gates.
"""
import itertools
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge import netlist as N

EVIDENCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evidence")
VECTORS = os.path.join(EVIDENCE, "tap02-vectors.json")


def hx(s: str) -> bytes:
    return bytes.fromhex(s.removeprefix("0x"))


class TestRecords(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        b = N.NetlistBuilder(3)
        a, c = b.input(0), b.input(1)
        b.xor(a, c)
        b.and_(a, b.input(2))
        data = N.encode(b.elements)
        els = N.decode(data, 3)
        self.assertEqual(N.encode(els), data)

    def test_truncated_record_rejected(self):
        data = N.encode([N.Element(N.NAND, [0, 1])])
        with self.assertRaises(N.IllFormed):
            N.decode(data[:-1], 2)

    def test_unknown_opcode_rejected(self):
        with self.assertRaises(N.IllFormed):
            N.decode(bytes([0x03, 0, 0, 0, 0, 0, 0]), 0)

    def test_forward_reference_rejected(self):
        # NAND whose inputs are signals it does not yet have
        data = N.encode([N.Element(N.NAND, [2, 2])])
        with self.assertRaises(N.IllFormed):
            N.load(data, 0, 1)

    def test_latch_may_refer_forward(self):
        # a LATCH whose d refers forward is legal (feedback)
        b = N.NetlistBuilder(1)
        latch_out = b.next_signal
        # emit latch then a NAND that feeds the latch d
        e = N.Element(N.LATCH, [b.next_signal + 1])
        b.elements.append(e)
        b.next_signal += 1
        b.nand(b.input(0), latch_out)
        data = N.encode(b.elements)
        c = N.load(data, 1, 1)
        self.assertEqual(c.n_state, 1)


class TestGates(unittest.TestCase):
    def _truth(self, build, n_in):
        b = N.NetlistBuilder(n_in)
        out = build(b)
        c = N.load(N.encode(b.elements), n_in, len(out))
        table = {}
        for combo in itertools.product([0, 1], repeat=n_in):
            _ns, o = N.beat(c, [], list(combo))
            table[combo] = N.decode_uint(o)
        return table

    def test_and_or_xor_nand(self):
        self.assertEqual(self._truth(lambda b: [b.and_(b.input(0), b.input(1))], 2),
                         {(0, 0): 0, (0, 1): 0, (1, 0): 0, (1, 1): 1})
        self.assertEqual(self._truth(lambda b: [b.or_(b.input(0), b.input(1))], 2),
                         {(0, 0): 0, (0, 1): 1, (1, 0): 1, (1, 1): 1})
        self.assertEqual(self._truth(lambda b: [b.xor(b.input(0), b.input(1))], 2),
                         {(0, 0): 0, (0, 1): 1, (1, 0): 1, (1, 1): 0})

    def test_gt_exhaustive_2bit(self):
        b = N.NetlistBuilder(4)
        g = N.gt(b, [b.input(0), b.input(1)], [b.input(2), b.input(3)])
        c = N.load(N.encode(b.elements), 4, len(b.elements))
        idx = g - (2 + 4)
        for x, y in itertools.product(range(4), repeat=2):
            bits = [(x >> k) & 1 for k in range(2)] + [(y >> k) & 1 for k in range(2)]
            _ns, o = N.beat(c, [], bits)
            self.assertEqual(o[idx], 1 if x > y else 0, (x, y))

    def test_add_exhaustive_2bit(self):
        b = N.NetlistBuilder(4)
        s, carry = N.add(b, [b.input(0), b.input(1)], [b.input(2), b.input(3)])
        outs = [b.not_(s[0]), b.not_(b.not_(s[0]))]  # placeholder to keep ordering
        # rebuild cleanly: expose s[0], s[1], carry as the trailing signals
        b2 = N.NetlistBuilder(4)
        s2, carry2 = N.add(b2, [b2.input(0), b2.input(1)], [b2.input(2), b2.input(3)])
        tmp = [b2.not_(x) for x in (s2 + [carry2])]
        for t in tmp:
            b2.not_(t)
        c = N.load(N.encode(b2.elements), 4, 3)
        for x, y in itertools.product(range(4), repeat=2):
            bits = [(x >> k) & 1 for k in range(2)] + [(y >> k) & 1 for k in range(2)]
            _ns, o = N.beat(c, [], bits)
            self.assertEqual(N.decode_uint(o), x + y, (x, y))

    def test_mul_const(self):
        for k in range(8):
            b = N.NetlistBuilder(3)
            p = N.mul_const(b, [b.input(i) for i in range(3)], k, 8)
            tmp = [b.not_(x) for x in p]
            for t in tmp:
                b.not_(t)
            c = N.load(N.encode(b.elements), 3, 8)
            for x in range(8):
                bits = [(x >> i) & 1 for i in range(3)]
                _ns, o = N.beat(c, [], bits)
                self.assertEqual(N.decode_uint(o), x * k, (x, k))


@unittest.skipUnless(os.path.exists(VECTORS), "TAP-02 vectors not present")
class TestOfficialVectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(VECTORS) as f:
            cls.v = json.load(f)

    def test_vector_file_integrity(self):
        import hashlib
        with open(VECTORS, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(digest,
                         "1a02f5cf991c130a22dc6cc6ee93d0f8fe0720afed081665fa8b8018aefd203b")

    def test_valid_vectors(self):
        for case in self.v["valid"]:
            with self.subTest(case=case["name"]):
                refs = case.get("refs", [])

                def resolve(cpu, cid, refs=refs):
                    for r in refs:
                        if hx(r["cpu"]) == cpu and r["id"] == cid:
                            return N.load(hx(r["netlist"]), r["nIn"], r["nOut"])
                    return None

                c = N.load(hx(case["netlist"]), case["nIn"], case["nOut"], resolve=resolve)
                if "truthTable" in case:
                    for row in case["truthTable"]:
                        inp = N.unpack(hx(row["inputs"]), c.n_in)
                        exp = N.unpack(hx(row["outputs"]), c.n_out)
                        _ns, out = N.beat(c, [0] * c.n_state, inp)
                        self.assertEqual(out, exp)
                elif "beats" in case:
                    st = [0] * c.n_state
                    for row in case["beats"]:
                        inp = N.unpack(hx(row["inputs"]), c.n_in)
                        ns, out = N.beat(c, st, inp)
                        self.assertEqual(out, N.unpack(hx(row["outputs"]), c.n_out))
                        if "newState" in row:
                            self.assertEqual(ns, N.unpack(hx(row["newState"]), c.n_state))
                        st = ns

    def test_illformed_vectors_rejected(self):
        for case in self.v["illFormed"]:
            with self.subTest(case=case["name"]):
                with self.assertRaises(N.IllFormed):
                    N.load(hx(case["netlist"]), case.get("nIn", 0), case.get("nOut", 0),
                           resolve=lambda a, b: None)

    def test_packing_edge_cases(self):
        pe = self.v["packingEdgeCases"]
        name = pe["circuit"]
        circuit_case = next(c for c in self.v["valid"] if c["name"] == name)
        c = N.load(hx(circuit_case["netlist"]), circuit_case["nIn"], circuit_case["nOut"])
        for case in pe["cases"]:
            with self.subTest(case=case["case"]):
                inp = hx(case["inputs"]) if case["inputs"] else b""
                _ns, out = N.beat(c, [], N.unpack(inp, c.n_in))
                self.assertEqual(N.pack(out).hex(), case["outputs"].removeprefix("0x"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
