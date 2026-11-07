"""The store contract: SQLite and PostgreSQL must behave identically.

Both :class:`tapejudge.store.Store` (SQLite) and
:class:`tapejudge.store_pg.PostgresStore` are held to one behavioural contract,
so the durable hosted store cannot silently drift from the local one. The SQLite
half always runs. The PostgreSQL half runs when ``TAPEJUDGE_TEST_DATABASE_URL``
points at a reachable server; otherwise it is skipped with a clear reason (a skip
is not a pass).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge.store import DuplicateError, Store


def _pg_factory():
    """Return a callable that makes a fresh PostgresStore, or None if unset."""
    dsn = os.environ.get("TAPEJUDGE_TEST_DATABASE_URL")
    if not dsn:
        return None
    import uuid

    from tapejudge.store_pg import PostgresStore

    def make():
        return PostgresStore(dsn, schema="c_" + uuid.uuid4().hex[:12])
    return make


class _ContractMixin:
    """The behaviour both stores must share. ``make_store`` is set by subclasses."""

    make_store = None  # type: ignore[assignment]

    def setUp(self):
        self.st = self.make_store()

    def tearDown(self):
        close = getattr(self.st, "close", None)
        if close is not None:
            close()

    def test_competition_lifecycle_and_freeze_guard(self):
        cid = self.st.create_competition(
            name="Demo", description="d", organiser="Org",
            opens_at="2026-01-01", closes_at="2026-02-01", judges=["alice", "bob"])
        comp = self.st.get_competition(cid)
        self.assertEqual(comp["status"], "draft")
        self.assertEqual(comp["owner_account_id"], None)
        self.assertIn(cid, [c["id"] for c in self.st.list_competitions()])

        # freeze refuses with no rubric
        with self.assertRaises(ValueError):
            self.st.freeze(cid, "key")

        self.st.set_rubric(cid, rubric_json='{"w":1}', rubric_digest="dig",
                           netlist_hex="00", netlist_sha256="sha",
                           n_in=2, n_out=1, gates=3, tie_break=["a"])
        self.st.freeze(cid, "commit-key")
        self.assertEqual(self.st.get_competition(cid)["status"], "frozen")

        # a frozen rubric cannot be replaced, and the panel cannot move
        with self.assertRaises(PermissionError):
            self.st.set_rubric(cid, rubric_json='{"w":2}', rubric_digest="x",
                               netlist_hex="01", netlist_sha256="y",
                               n_in=2, n_out=1, gates=3, tie_break=[])
        with self.assertRaises(PermissionError):
            self.st.set_judges(cid, ["carol"])
        with self.assertRaises(PermissionError):
            self.st.freeze(cid, "again")

    def test_entries_and_evaluations_are_append_only(self):
        cid = self.st.create_competition(
            name="Demo", description="", organiser="Org", opens_at="",
            closes_at="", judges=["alice"])
        eid = self.st.add_entry(cid, stable_id="e1", title="T", summary="s",
                                link="http://x", evidence={"k": 1},
                                content_hash="h1")
        self.assertEqual(self.st.get_entry_by_stable(cid, "e1")["id"], eid)
        self.assertEqual(len(self.st.list_entries(cid)), 1)

        with self.assertRaises(DuplicateError):
            self.st.add_entry(cid, stable_id="e1", title="T2", summary="",
                              link="", evidence={}, content_hash="h2")

        self.st.add_evaluation(cid, entry_id=eid, judge="alice",
                               scores={"a": 5}, justification="j",
                               commitment="c", nonce="n")
        self.assertTrue(self.st.has_evaluation(cid, eid, "alice"))
        self.assertFalse(self.st.has_evaluation(cid, eid, "bob"))
        with self.assertRaises(DuplicateError):
            self.st.add_evaluation(cid, entry_id=eid, judge="alice",
                                   scores={"a": 9}, justification="",
                                   commitment="c2", nonce="n")

    def test_conflict_is_upserted_not_duplicated(self):
        cid = self.st.create_competition(
            name="Demo", description="", organiser="Org", opens_at="",
            closes_at="", judges=["alice"])
        eid = self.st.add_entry(cid, stable_id="e1", title="T", summary="",
                                link="", evidence={}, content_hash="h")
        self.st.declare_conflict(cid, judge="alice", entry_id=eid, reason="r1")
        self.st.declare_conflict(cid, judge="alice", entry_id=eid, reason="r2")
        rows = self.st.list_conflicts(cid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "r2")

    def test_accounts_and_memberships(self):
        aid = self.st.create_account(email="a@x.io", display_name="A",
                                     password_hash="ph")
        self.assertEqual(self.st.get_account_by_email("a@x.io")["id"], aid)
        self.assertEqual(self.st.get_account(aid)["session_epoch"], 0)
        with self.assertRaises(DuplicateError):
            self.st.create_account(email="a@x.io", display_name="B",
                                   password_hash="ph2")

        self.st.bump_session_epoch(aid)
        self.assertEqual(self.st.get_account(aid)["session_epoch"], 1)
        self.st.set_password_hash(aid, "ph3")
        self.assertEqual(self.st.get_account(aid)["password_hash"], "ph3")

        cid = self.st.create_competition(
            name="Demo", description="", organiser="Org", opens_at="",
            closes_at="", judges=["alice"])
        mid = self.st.add_membership(account_id=aid, competition_id=cid,
                                     role="judge", label="alice")
        self.assertEqual(self.st.get_membership(cid, aid, "judge")["id"], mid)
        self.assertEqual(self.st.find_membership_by_label(cid, "alice")["id"], mid)
        self.assertEqual(len(self.st.list_memberships(cid)), 1)
        self.assertEqual(len(self.st.memberships_for_account(aid)), 1)
        # the same role twice, or a taken judge label, is refused
        with self.assertRaises(DuplicateError):
            self.st.add_membership(account_id=aid, competition_id=cid,
                                   role="judge", label="alice")
        self.st.remove_membership(mid)
        self.assertEqual(len(self.st.list_memberships(cid)), 0)

    def test_invitation_accepts_exactly_once(self):
        cid = self.st.create_competition(
            name="Demo", description="", organiser="Org", opens_at="",
            closes_at="", judges=["alice"])
        iid = self.st.create_invitation(
            competition_id=cid, email="j@x.io", role="judge", label="alice",
            token_hash="th1", expires_at="2099-01-01T00:00:00Z")
        self.assertEqual(self.st.get_invitation_by_token_hash("th1")["id"], iid)
        self.assertEqual(len(self.st.list_invitations(cid)), 1)
        self.assertTrue(self.st.accept_invitation(iid, "acct1"))
        self.assertFalse(self.st.accept_invitation(iid, "acct2"))
        self.assertEqual(self.st.get_invitation_by_token_hash("th1")["accepted_by"],
                         "acct1")

    def test_audit_log_redacts_personal_identifiers(self):
        cid = self.st.create_competition(
            name="Demo", description="", organiser="Org", opens_at="",
            closes_at="", judges=["alice"])
        self.st.log_event(cid, "judge_invited", {"label": "alice", "email": "j@x.io"})
        events = self.st.list_events(cid)
        kinds = [e["kind"] for e in events]
        self.assertIn("judge_invited", kinds)
        invited = next(e for e in events if e["kind"] == "judge_invited")
        self.assertNotIn("email", invited["payload_json"])
        self.assertIn("alice", invited["payload_json"])

    def test_status_and_publication_records(self):
        cid = self.st.create_competition(
            name="Demo", description="", organiser="Org", opens_at="",
            closes_at="", judges=[])
        self.st.set_status(cid, "judging")
        self.assertEqual(self.st.get_competition(cid)["status"], "judging")
        self.st.record_publication(cid, chain_id=196, processor="0xabc",
                                   circuit_id=7, tx_hash="0xdead", mode="prepared",
                                   fee_wei=12345)
        comp = self.st.get_competition(cid)
        self.assertEqual(comp["chain_id"], 196)
        self.assertEqual(comp["tx_hash"], "0xdead")
        self.assertEqual(comp["fee_wei"], "12345")
        self.st.mark_settled(cid)
        self.assertEqual(self.st.get_competition(cid)["status"], "settled")

    def test_schema_version_matches(self):
        from tapejudge.store import SCHEMA_VERSION
        self.assertEqual(self.st.schema_version(), SCHEMA_VERSION)


class TestSqliteContract(_ContractMixin, unittest.TestCase):
    make_store = staticmethod(lambda: Store(":memory:"))


@unittest.skipUnless(os.environ.get("TAPEJUDGE_TEST_DATABASE_URL"),
                     "set TAPEJUDGE_TEST_DATABASE_URL to run the Postgres contract")
class TestPostgresContract(_ContractMixin, unittest.TestCase):
    def setUp(self):
        factory = _pg_factory()
        self.make_store = staticmethod(factory)
        super().setUp()


if __name__ == "__main__":
    unittest.main()
