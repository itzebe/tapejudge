"""Publication confirmation tests — the read half of the TapeOut integration.

The product's promise is that a rubric is a frozen circuit, and that a competition
is only reported as *published on chain* when a real circuit exists that carries
the frozen netlist. This file pins the honesty of that transition:

* a competition never becomes ``onchain_verified`` from a *prepared* call alone;
* a receipt that is missing, reverted or mismatched is reported honestly and
  leaves the competition prepared;
* only a successful receipt **and** a byte-for-byte netlist match marks it
  published.

Everything here is synthetic and offline: the chain client is a fake, so no
network, wallet, key or transaction is involved.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge import Store, TapeJudgeService
from tapejudge.service import (
    PUBLISH_ONCHAIN_PENDING_SIGNATURE,
    PUBLISH_ONCHAIN_VERIFIED,
    ValidationError,
)

CRITERIA = [{"name": "demo", "weight": 3, "max_score": 10},
            {"name": "tests", "weight": 2, "max_score": 10}]


class _ConfirmChain:
    """A fake chain that answers publication confirmation deterministically.

    ``verdict`` is what ``verify_publication`` returns; ``receipt`` is what the
    receipt read returns. Nothing here touches the network.
    """

    def __init__(self, *, verdict="match", receipt=None, netlist=b"\x01\x02"):
        self._verdict = verdict
        self._receipt = receipt
        self.netlist = netlist
        self.processor = "0xprocessor"
        self.circuit_id = 103

    def prepare_tapeout(self, netlist, n_in, n_out, n_gates=None):
        self.netlist = netlist
        return {"mode": PUBLISH_ONCHAIN_PENDING_SIGNATURE, "chain_id": 196,
                "processor": self.processor, "circuit_id": self.circuit_id,
                "tx": {"unsigned": True}, "fee_wei": 1300000000000000,
                "fee_known": True, "netlist_bytes": len(netlist),
                "note": "stub", "blocked_reason": None}

    def is_reachable(self):
        return True

    def get_receipt(self, tx_hash):
        return self._receipt

    def verify_publication(self, circuit_id, expected_netlist, processor=None):
        return {"processor": self.processor, "circuit_id": circuit_id,
                "expected_netlist_sha256": "aa", "chain_netlist_sha256": "bb",
                "chain_info": {"nIn": 1, "nOut": 1, "nState": 0, "gateCount": 4},
                "verdict": self._verdict, "reason": f"stub {self._verdict}"}


def build(chain=None):
    svc = TapeJudgeService(Store(":memory:"), chain_client=chain or _ConfirmChain())
    cid = svc.create_competition(name="Synthetic Round", organiser="SYNTH",
                                 judges=["j1", "j2"])
    svc.set_rubric(cid, CRITERIA, judges=2, tie_break=["demo", "tests"])
    svc.freeze(cid)
    return svc, cid


class TestConfirmPublication(unittest.TestCase):
    def test_successful_receipt_and_matching_netlist_marks_published(self):
        chain = _ConfirmChain(verdict="match", receipt={"status": "0x1"})
        svc, cid = build(chain)
        svc.publish(cid)
        out = svc.confirm_publication(cid, tx_hash="0x" + "ab" * 32)
        self.assertTrue(out["verified"])
        comp = svc.get_competition(cid)
        self.assertEqual(comp["publish_mode"], PUBLISH_ONCHAIN_VERIFIED)
        self.assertEqual(comp["tx_hash"], "0x" + "ab" * 32)
        self.assertTrue(comp["publication"]["on_chain"])

    def test_missing_receipt_is_pending_and_not_published(self):
        chain = _ConfirmChain(verdict="match", receipt=None)
        svc, cid = build(chain)
        svc.publish(cid)
        out = svc.confirm_publication(cid, tx_hash="0x" + "cd" * 32)
        self.assertFalse(out["verified"])
        self.assertEqual(out["state"], "pending")
        self.assertNotEqual(svc.get_competition(cid)["publish_mode"],
                            PUBLISH_ONCHAIN_VERIFIED)

    def test_reverted_receipt_is_not_published(self):
        chain = _ConfirmChain(verdict="match", receipt={"status": "0x0"})
        svc, cid = build(chain)
        svc.publish(cid)
        out = svc.confirm_publication(cid, tx_hash="0x" + "ef" * 32)
        self.assertFalse(out["verified"])
        self.assertEqual(out["state"], "reverted")
        self.assertNotEqual(svc.get_competition(cid)["publish_mode"],
                            PUBLISH_ONCHAIN_VERIFIED)

    def test_netlist_mismatch_is_not_published(self):
        # A successful receipt for a circuit that does NOT carry the frozen
        # netlist must never be accepted as this competition's publication.
        chain = _ConfirmChain(verdict="mismatch", receipt={"status": "0x1"})
        svc, cid = build(chain)
        svc.publish(cid)
        out = svc.confirm_publication(cid, tx_hash="0x" + "11" * 32)
        self.assertFalse(out["verified"])
        self.assertEqual(out["state"], "mismatch")
        self.assertNotEqual(svc.get_competition(cid)["publish_mode"],
                            PUBLISH_ONCHAIN_VERIFIED)

    def test_circuit_not_found_is_not_published(self):
        chain = _ConfirmChain(verdict="not_found", receipt={"status": "0x1"})
        svc, cid = build(chain)
        svc.publish(cid)
        out = svc.confirm_publication(cid, tx_hash="0x" + "22" * 32)
        self.assertFalse(out["verified"])
        self.assertEqual(out["state"], "not_found")

    def test_rpc_failure_is_unavailable_not_a_false_success(self):
        class _Dead(_ConfirmChain):
            def get_receipt(self, tx_hash):
                raise RuntimeError("rpc down")

        svc, cid = build(_Dead())
        svc.publish(cid)
        out = svc.confirm_publication(cid, tx_hash="0x" + "33" * 32)
        self.assertFalse(out["verified"])
        self.assertEqual(out["state"], "unavailable")

    def test_draft_cannot_confirm(self):
        chain = _ConfirmChain(receipt={"status": "0x1"})
        svc = TapeJudgeService(Store(":memory:"), chain_client=chain)
        cid = svc.create_competition(name="D", organiser="SYNTH", judges=["j1"])
        with self.assertRaises(ValidationError):
            svc.confirm_publication(cid, tx_hash="0x" + "44" * 32)


if __name__ == "__main__":
    unittest.main()
