"""Live X Layer verification tests.

These call the deployed TapeOut processor contracts over JSON-RPC with
`eth_call` only (read-only). They are skipped when the network is unavailable so
the rest of the suite still runs offline. Set TAPEJUDGE_SKIP_CHAIN=1 to skip
deliberately.

What they prove: the local TAP-02 implementation in this package computes the
same result as the live X Layer contracts, for real circuits that already exist
on chain. That is the bridge between "a local simulation" and "the chain".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKIP = os.environ.get("TAPEJUDGE_SKIP_CHAIN") == "1"

try:
    from tapejudge import chain
    _probe = chain.block_number()
    _ONLINE = True
except Exception:
    _ONLINE = False

X_LAYER_FACTORY = "0x1f09DAeFA827f02CBb40967cc91b259763760761"


@unittest.skipIf(SKIP, "TAPEJUDGE_SKIP_CHAIN=1")
@unittest.skipUnless(_ONLINE, "X Layer RPC not reachable")
class TestLiveChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.factory = X_LAYER_FACTORY
        cls.cpu0 = chain.cpu_at(cls.factory, 0)

    def test_factory_lists_processors(self):
        n = chain.cpu_count(self.factory)
        self.assertGreater(n, 0)
        self.assertTrue(self.cpu0.startswith("0x") and len(self.cpu0) == 42)

    def test_local_matches_chain_for_every_combinational_circuit(self):
        """For every combinational circuit on processor 0, local == chain."""
        nid = chain.next_id(self.cpu0)
        checked = 0
        for cid in range(1, nid):
            info = chain.circuit_info(self.cpu0, cid)
            if info["nState"] != 0 or info["nIn"] > 8:
                continue
            samples = [bytes([b]) for b in range(1 << info["nIn"])]
            rep = chain.verify_circuit_against_chain(self.cpu0, cid, samples)
            self.assertTrue(rep["match"], f"circuit {cid} mismatch")
            checked += 1
        self.assertGreaterEqual(checked, 1, "no combinational circuits found to check")

    def test_sequential_circuit_rejects_eval(self):
        """A sequential circuit must revert eval with 'has latch: use step'."""
        nid = chain.next_id(self.cpu0)
        for cid in range(1, nid):
            info = chain.circuit_info(self.cpu0, cid)
            if info["nState"] > 0:
                with self.assertRaises(chain.RpcError) as ctx:
                    chain.eval_circuit(self.cpu0, cid, bytes([0]))
                self.assertIn("has latch", str(ctx.exception).lower())
                return
        self.skipTest("no sequential circuit found")

    def test_step_returns_new_state_and_outputs(self):
        nid = chain.next_id(self.cpu0)
        for cid in range(1, nid):
            info = chain.circuit_info(self.cpu0, cid)
            if info["nState"] > 0:
                st = bytes((info["nState"] + 7) // 8)
                new_state, outs = chain.step_circuit(self.cpu0, cid, st, bytes([0]))
                self.assertEqual(len(new_state), (info["nState"] + 7) // 8)
                self.assertEqual(len(outs), (info["nOut"] + 7) // 8)
                return
        self.skipTest("no sequential circuit found")

    def test_beacon_is_ownerless_so_implementation_is_frozen(self):
        """The circuit beacon owner is the zero address -> circuits cannot change."""
        from tapejudge import chain as ch
        beacon = "0xf70d1ed4f62cf3780157b0b421b7e2f45bd0991c"
        owner = ch.eth_call(beacon, "0x8da5cb5b")  # owner()
        self.assertEqual(owner, "0x" + "0" * 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
