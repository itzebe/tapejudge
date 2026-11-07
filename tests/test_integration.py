"""X Layer integration tests.

Two groups:

* **Offline (always run).** The selector arithmetic, the tapeout calldata, and
  the degradation behaviour when the RPC is unreachable. These are deterministic
  and must pass with no network.

* **Live (gated on TAPEJUDGE_SKIP_CHAIN).** Read-only ``eth_call`` checks against
  the real X Layer contracts, using the same verified client as the proof page.
  Skipped by default in offline runs; set ``TAPEJUDGE_SKIP_CHAIN=1`` to skip
  explicitly, or unset it to run them (they need network access).

No test here sends a transaction, connects a wallet, or spends anything.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge.xlayer import (
    TAPEOUT_SELECTOR,
    XLayerClient,
    _encode_tapeout_args,
    keccak256,
    selector,
)

SKIP_LIVE = os.environ.get("TAPEJUDGE_SKIP_CHAIN") == "1"


class TestSelectors(unittest.TestCase):
    def test_keccak_known_vectors(self):
        self.assertEqual(keccak256(b"").hex(),
                         "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(keccak256(b"abc").hex(),
                         "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")

    def test_tapeout_selector_matches_tap02(self):
        self.assertEqual(selector("tapeout(bytes,uint32,uint32)"), "0x7bd3ac1d")
        self.assertEqual(TAPEOUT_SELECTOR, "0x7bd3ac1d")

    def test_read_selectors_match_tap02(self):
        self.assertEqual(selector("eval(uint256,bytes)"), "0x934d06ea")
        self.assertEqual(selector("circuitInfo(uint256)"), "0x084d60f1")
        self.assertEqual(selector("netlist(uint256)"), "0x3fc4be56")
        self.assertEqual(selector("nextId()"), "0x61b8ce8c")
        self.assertEqual(selector("cpuCount()"), "0xa94da8a7")
        self.assertEqual(selector("cpuAt(uint256)"), "0x4bc7cbbd")


class TestTapeoutEncoding(unittest.TestCase):
    def test_args_are_abi_encoded(self):
        netlist = bytes([0x00, 0x01, 0x02])
        enc = _encode_tapeout_args(netlist, 12, 25)
        # head: offset=0x60, nIn=12, nOut=25 ; tail: length=3 + padded bytes
        self.assertTrue(enc.startswith(f"{0x60:064x}{12:064x}{25:064x}"))
        self.assertIn(f"{3:064x}", enc)

    def test_prepare_degrades_when_rpc_unreachable(self):
        client = XLayerClient(rpc_url="http://127.0.0.1:1", allow_send=False)
        out = client.prepare_tapeout(b"\x00\x01", 4, 2)
        self.assertEqual(out["mode"], "local_only")
        self.assertEqual(out["blocked_reason"], "rpc_unreachable")
        self.assertIsNone(out["tx"])
        self.assertFalse(out["fee_known"])
        self.assertIsNone(out["fee_wei"])


@unittest.skipIf(SKIP_LIVE, "live chain tests skipped (TAPEJUDGE_SKIP_CHAIN=1)")
class TestLiveXLayer(unittest.TestCase):
    """Read-only checks against the real X Layer contracts. Never a transaction."""

    @classmethod
    def setUpClass(cls):
        cls.client = XLayerClient()
        try:
            cls.client.block_number()
        except Exception as e:
            raise unittest.SkipTest(f"X Layer RPC unreachable: {e}") from e

    def test_factory_and_processors(self):
        self.assertGreater(self.client.cpu_count(), 0)
        proc = self.client.processor_at(0)
        self.assertTrue(proc.startswith("0x") and len(proc) == 42)
        self.assertGreaterEqual(self.client.next_id(proc), 0)

    def test_existing_circuit_matches_locally(self):
        """An existing on-chain circuit evaluates the same locally and on chain."""
        proc = self.client.processor_at(0)
        # circuit #1 is a small combinational circuit (TAP-02 test vector context)
        info = self.client.circuit_info(1, proc)
        if info["nState"] != 0:
            self.skipTest("circuit #1 is sequential")
        rep = self.client.verify_circuit_against_chain(
            1, [bytes([0]), bytes([1]), bytes([2]), bytes([3])], proc)
        self.assertTrue(rep["match"])


if __name__ == "__main__":
    unittest.main()
