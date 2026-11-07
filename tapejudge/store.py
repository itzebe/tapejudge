"""SQLite persistence for TapeJudge.

The database is the *store of inputs*, never the store of a result: a ranking is
always recomputed from the frozen rubric and the stored evaluations, so a result
cannot be edited into the database. Everything here is a plain, local file — no
server, no paid service, no network.

Design rules that the rest of the app depends on:

* A rubric is stored once, at freeze, together with its digest and the compiled
  netlist hash. ``update_rubric`` refuses to run on a frozen competition, which is
  what makes "a published rubric cannot be silently replaced" true rather than
  merely intended.
* Evaluations are append-only. ``insert_evaluation`` enforces a UNIQUE
  (competition, entry, judge) constraint, so a second, different submission is a
  database error, not a code path that might forget to check.
* ``audit_events`` is an append-only log. It is how a correction or an appeal is
  *recorded* without mutating the inputs it refers to.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import ClassVar

SCHEMA_VERSION = 2

# Event payload keys that must never be served from the public audit endpoint.
# The audit trail is public (the product's promise is that anyone can verify),
# so an invitee's email address has no business in it: a judge invitation is an
# organiser-only action, and the address is a personal identifier. Recording the
# label alone still proves a panel member was invited.
_REDACTED_EVENT_KEYS = frozenset({"email"})


def redact_event_payload(payload: dict) -> dict:
    """Drop personal identifiers from an audit payload before it is served.

    Applied at the read boundary, so rows written before this rule existed are
    redacted too, not only new ones.
    """
    return {k: v for k, v in payload.items() if k not in _REDACTED_EVENT_KEYS}


class DuplicateError(RuntimeError):
    """A UNIQUE constraint was violated.

    The service checks for duplicates before inserting, but that check and the
    insert are not one atomic step: two concurrent requests can both pass the
    check and then race to insert. The database's UNIQUE constraint is the real
    guard, and this exception is how that guard surfaces — as a clean 409, never
    as an unhandled ``sqlite3.IntegrityError`` (which would be a 500).
    """

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS competitions (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    organiser     TEXT NOT NULL DEFAULT '',
    owner_account_id TEXT,                          -- NULL for pre-auth competitions
    opens_at      TEXT NOT NULL DEFAULT '',
    closes_at     TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'draft',   -- draft|frozen|open|judging|settled
    rubric_json   TEXT,
    rubric_digest TEXT,
    netlist_hex   TEXT,
    netlist_sha256 TEXT,
    n_in          INTEGER,
    n_out         INTEGER,
    gates         INTEGER,
    judges_json   TEXT NOT NULL DEFAULT '[]',
    tie_break_json TEXT NOT NULL DEFAULT '[]',
    commit_key    TEXT,
    frozen_at     TEXT,
    settled_at    TEXT,
    created_at    TEXT NOT NULL,
    -- publication evidence; NULL until a real publication happens
    chain_id      INTEGER,
    processor     TEXT,
    circuit_id    INTEGER,
    tx_hash       TEXT,
    published_at  TEXT,
    publish_mode  TEXT NOT NULL DEFAULT 'not_published',
    fee_wei       TEXT,
    prize_note    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS entries (
    id            TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    stable_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    link          TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    content_hash  TEXT NOT NULL,
    private       INTEGER NOT NULL DEFAULT 0,
    submitter_account_id TEXT,                     -- NULL for pre-auth entries
    submitted_at  TEXT NOT NULL,
    UNIQUE (competition_id, stable_id)
);
CREATE TABLE IF NOT EXISTS evaluations (
    id             TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    entry_id       TEXT NOT NULL REFERENCES entries(id),
    judge          TEXT NOT NULL,
    judge_account_id TEXT,                         -- NULL => identity not authenticated
    scores_json    TEXT NOT NULL,
    justification  TEXT NOT NULL DEFAULT '',
    commitment     TEXT NOT NULL,
    nonce          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    UNIQUE (competition_id, entry_id, judge)
);
CREATE TABLE IF NOT EXISTS accounts (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    session_epoch INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    UNIQUE (email)
);
CREATE TABLE IF NOT EXISTS memberships (
    id             TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL REFERENCES accounts(id),
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    role           TEXT NOT NULL,                  -- organiser|judge|entrant
    label          TEXT NOT NULL DEFAULT '',       -- the judge identifier on this competition
    created_at     TEXT NOT NULL,
    UNIQUE (competition_id, account_id, role)
);
-- A judge label is unique per competition; organiser/entrant rows leave it ''.
CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_judge_label
    ON memberships(competition_id, label) WHERE role='judge';
CREATE TABLE IF NOT EXISTS invitations (
    id             TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    email          TEXT NOT NULL,
    role           TEXT NOT NULL,
    label          TEXT NOT NULL,
    token_hash     TEXT NOT NULL,
    invited_by     TEXT,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    accepted_at    TEXT,
    accepted_by    TEXT,
    revoked_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_invite_token ON invitations(token_hash);
CREATE TABLE IF NOT EXISTS conflicts (
    id             TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    judge          TEXT NOT NULL,
    entry_id       TEXT REFERENCES entries(id),
    reason         TEXT NOT NULL DEFAULT '',
    declared_at    TEXT NOT NULL,
    UNIQUE (competition_id, judge, entry_id)
);
CREATE TABLE IF NOT EXISTS audit_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    competition_id TEXT NOT NULL,
    kind           TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_comp ON entries(competition_id);
CREATE INDEX IF NOT EXISTS idx_evals_comp ON evaluations(competition_id);
CREATE INDEX IF NOT EXISTS idx_audit_comp ON audit_events(competition_id, id);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    """A thin, thread-safe SQLite wrapper. One file; no server."""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._lock = threading.RLock()
        # One persistent connection per Store. A per-call connection would lose
        # an in-memory database, and the shared-cache URI would leak state between
        # unrelated Store instances in the same process (which would break test
        # isolation). A single connection with an RLock is correct for SQLite and
        # keeps each Store's data private.
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn_obj = sqlite3.connect(path, check_same_thread=False)
        self._conn_obj.row_factory = sqlite3.Row
        # WAL + a busy timeout make concurrent readers and a single writer safe on
        # a file database (the WAL mode is a no-op for :memory:). FULL synchronous
        # keeps a committed write durable across a process crash, which matters for
        # a scoring record that must survive a restart.
        if path != ":memory:":
            self._conn_obj.execute("PRAGMA journal_mode=WAL")
            self._conn_obj.execute("PRAGMA synchronous=FULL")
        self._conn_obj.execute("PRAGMA busy_timeout=5000")
        self._conn_obj.execute("PRAGMA foreign_keys=ON")
        # Close the connection when the Store is collected, so tests and short
        # scripts do not leak file handles (SQLite warns otherwise).
        import weakref
        self._finalizer = weakref.finalize(self, self._conn_obj.close)
        self._init()

    @contextmanager
    def _conn(self):
        with self._lock:
            try:
                yield self._conn_obj
            except Exception:
                # A statement that raised leaves the implicit transaction open;
                # without an explicit rollback the next block would commit that
                # half-finished state. Roll back, then let the error surface.
                self._conn_obj.rollback()
                raise
            else:
                self._conn_obj.commit()

    def close(self) -> None:
        with self._lock:
            self._conn_obj.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)
            self._migrate(c)
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                      (str(SCHEMA_VERSION),))

    # Additive migrations for a database created by an earlier version. Each
    # step is idempotent (it checks ``PRAGMA table_info`` first), so running it
    # on an already-migrated database is a no-op and a partial migration can be
    # re-run safely. No column is dropped and no row is deleted: the migration
    # only ever *adds* nullable columns, so existing competition, entry and
    # evaluation records are preserved byte-for-byte.
    _ADDED_COLUMNS: ClassVar[dict[str, dict[str, str]]] = {
        "competitions": {"owner_account_id": "TEXT"},
        "entries": {"submitter_account_id": "TEXT"},
        "evaluations": {"judge_account_id": "TEXT"},
    }

    def _migrate(self, c) -> None:
        for table, columns in self._ADDED_COLUMNS.items():
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def schema_version(self) -> int:
        """The schema version recorded in the database (not the code constant)."""
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row else 0

    # -- competitions ------------------------------------------------------- #
    def create_competition(self, *, name, description, organiser, opens_at, closes_at,
                           judges, prize_note="", owner_account_id=None) -> str:
        cid = new_id("comp")
        with self._conn() as c:
            c.execute(
                "INSERT INTO competitions(id,name,description,organiser,opens_at,closes_at,"
                "status,judges_json,tie_break_json,created_at,prize_note,owner_account_id) "
                "VALUES(?,?,?,?,?,?,'draft',?,?,?,?,?)",
                (cid, name, description, organiser, opens_at, closes_at,
                 json.dumps(list(judges)), "[]", _now(), prize_note, owner_account_id))
        self.log_event(cid, "competition_created", {"name": name, "judges": list(judges)})
        return cid

    def get_competition(self, cid: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM competitions WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None

    def list_competitions(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM competitions ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def set_rubric(self, cid: str, *, rubric_json, rubric_digest, netlist_hex,
                   netlist_sha256, n_in, n_out, gates, tie_break) -> None:
        """Store the compiled rubric. Refuses if the competition is already frozen."""
        with self._conn() as c:
            row = c.execute("SELECT status FROM competitions WHERE id=?", (cid,)).fetchone()
            if row is None:
                raise KeyError(cid)
            if row["status"] != "draft":
                raise PermissionError(
                    "rubric is frozen; a published rubric cannot be replaced")
            c.execute(
                "UPDATE competitions SET rubric_json=?, rubric_digest=?, netlist_hex=?, "
                "netlist_sha256=?, n_in=?, n_out=?, gates=?, tie_break_json=? WHERE id=?",
                (rubric_json, rubric_digest, netlist_hex, netlist_sha256,
                 n_in, n_out, gates, json.dumps(list(tie_break)), cid))

    def freeze(self, cid: str, commit_key: str) -> None:
        with self._conn() as c:
            row = c.execute("SELECT status, rubric_json FROM competitions WHERE id=?",
                            (cid,)).fetchone()
            if row is None:
                raise KeyError(cid)
            if not row["rubric_json"]:
                raise ValueError("cannot freeze a competition with no rubric")
            if row["status"] != "draft":
                raise PermissionError("competition is already frozen")
            c.execute("UPDATE competitions SET status='frozen', commit_key=?, frozen_at=? "
                      "WHERE id=?", (commit_key, _now(), cid))

    def set_judges(self, cid: str, judges: list[str]) -> None:
        """Replace the declared panel. Only meaningful while the competition is a
        draft; a frozen panel is part of the frozen rule and must not move."""
        with self._conn() as c:
            row = c.execute("SELECT status FROM competitions WHERE id=?", (cid,)).fetchone()
            if row is None:
                raise KeyError(cid)
            if row["status"] != "draft":
                raise PermissionError("the panel is frozen and cannot be changed")
            c.execute("UPDATE competitions SET judges_json=? WHERE id=?",
                      (json.dumps(list(judges)), cid))

    def set_status(self, cid: str, status: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE competitions SET status=? WHERE id=?", (status, cid))

    def record_publication(self, cid: str, *, chain_id, processor, circuit_id, tx_hash,
                           mode, fee_wei=None) -> None:
        with self._conn() as c:
            c.execute("UPDATE competitions SET chain_id=?, processor=?, circuit_id=?, "
                      "tx_hash=?, published_at=?, publish_mode=?, fee_wei=? WHERE id=?",
                      (chain_id, processor, circuit_id, tx_hash, _now(), mode,
                       None if fee_wei is None else str(fee_wei), cid))

    def mark_settled(self, cid: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE competitions SET status='settled', settled_at=? WHERE id=?",
                      (_now(), cid))

    # -- entries ------------------------------------------------------------ #
    def add_entry(self, cid: str, *, stable_id, title, summary, link, evidence,
                  content_hash, private=False, submitter_account_id=None) -> str:
        eid = new_id("entry")
        with self._conn() as c:
            try:
                c.execute(
                    "INSERT INTO entries(id,competition_id,stable_id,title,summary,link,"
                    "evidence_json,content_hash,private,submitted_at,submitter_account_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, cid, stable_id, title, summary, link,
                     json.dumps(evidence or {}), content_hash, 1 if private else 0, _now(),
                     submitter_account_id))
            except sqlite3.IntegrityError as e:
                raise DuplicateError(
                    f"entry {stable_id!r} already exists in this competition") from e
        self.log_event(cid, "entry_submitted", {"entry": stable_id, "content_hash": content_hash})
        return eid

    def list_entries(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM entries WHERE competition_id=? "
                             "ORDER BY submitted_at", (cid,)).fetchall()
        return [dict(r) for r in rows]

    def get_entry_by_stable(self, cid: str, stable_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM entries WHERE competition_id=? AND stable_id=?",
                            (cid, stable_id)).fetchone()
        return dict(row) if row else None

    # -- evaluations -------------------------------------------------------- #
    def add_evaluation(self, cid: str, *, entry_id, judge, scores, justification,
                       commitment, nonce, judge_account_id=None) -> str:
        eid = new_id("eval")
        with self._conn() as c:
            try:
                c.execute(
                    "INSERT INTO evaluations(id,competition_id,entry_id,judge,scores_json,"
                    "justification,commitment,nonce,created_at,judge_account_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (eid, cid, entry_id, judge, json.dumps(scores, sort_keys=True),
                     justification, commitment, nonce, _now(), judge_account_id))
            except sqlite3.IntegrityError as e:
                raise DuplicateError(
                    f"{judge!r} has already scored entry {entry_id!r}") from e
        self.log_event(cid, "evaluation_committed",
                       {"entry_id": entry_id, "judge": judge, "commitment": commitment})
        return eid

    def list_evaluations(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM evaluations WHERE competition_id=? "
                             "ORDER BY created_at", (cid,)).fetchall()
        return [dict(r) for r in rows]

    def has_evaluation(self, cid: str, entry_id: str, judge: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM evaluations WHERE competition_id=? AND entry_id=? "
                            "AND judge=?", (cid, entry_id, judge)).fetchone()
        return row is not None

    # -- conflicts ---------------------------------------------------------- #
    def declare_conflict(self, cid: str, *, judge, entry_id, reason) -> str:
        fid = new_id("coi")
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO conflicts(id,competition_id,judge,entry_id,"
                      "reason,declared_at) VALUES(?,?,?,?,?,?)",
                      (fid, cid, judge, entry_id, reason, _now()))
        self.log_event(cid, "conflict_declared",
                       {"judge": judge, "entry_id": entry_id, "reason": reason})
        return fid

    def list_conflicts(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM conflicts WHERE competition_id=?", (cid,)).fetchall()
        return [dict(r) for r in rows]

    # -- accounts ----------------------------------------------------------- #
    def create_account(self, *, email, display_name, password_hash) -> str:
        aid = new_id("acct")
        with self._conn() as c:
            try:
                c.execute(
                    "INSERT INTO accounts(id,email,display_name,password_hash,"
                    "session_epoch,created_at) VALUES(?,?,?,?,0,?)",
                    (aid, email, display_name, password_hash, _now()))
            except sqlite3.IntegrityError as e:
                raise DuplicateError("an account with that email already exists") from e
        return aid

    def get_account(self, aid: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
        return dict(row) if row else None

    def get_account_by_email(self, email: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None

    def set_password_hash(self, aid: str, password_hash: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE accounts SET password_hash=? WHERE id=?", (password_hash, aid))

    def bump_session_epoch(self, aid: str) -> None:
        """Invalidate every existing session for an account (logout everywhere,
        or after a password change)."""
        with self._conn() as c:
            c.execute("UPDATE accounts SET session_epoch=session_epoch+1 WHERE id=?", (aid,))

    # -- memberships -------------------------------------------------------- #
    def add_membership(self, *, account_id, competition_id, role, label) -> str:
        mid = new_id("mem")
        with self._conn() as c:
            try:
                c.execute(
                    "INSERT INTO memberships(id,account_id,competition_id,role,label,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (mid, account_id, competition_id, role, label, _now()))
            except sqlite3.IntegrityError as e:
                raise DuplicateError(
                    "that account already has this role here, or the label is taken") from e
        return mid

    def list_memberships(self, competition_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM memberships WHERE competition_id=? ORDER BY created_at",
                (competition_id,)).fetchall()
        return [dict(r) for r in rows]

    def memberships_for_account(self, account_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM memberships WHERE account_id=? ORDER BY created_at",
                (account_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_membership(self, competition_id: str, account_id: str, role: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM memberships WHERE competition_id=? AND account_id=? AND role=?",
                (competition_id, account_id, role)).fetchone()
        return dict(row) if row else None

    def find_membership_by_label(self, competition_id: str, label: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM memberships WHERE competition_id=? AND label=?",
                (competition_id, label)).fetchone()
        return dict(row) if row else None

    def remove_membership(self, membership_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM memberships WHERE id=?", (membership_id,))

    # -- invitations -------------------------------------------------------- #
    def create_invitation(self, *, competition_id, email, role, label, token_hash,
                          expires_at, invited_by=None) -> str:
        iid = new_id("inv")
        with self._conn() as c:
            c.execute(
                "INSERT INTO invitations(id,competition_id,email,role,label,token_hash,"
                "invited_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (iid, competition_id, email, role, label, token_hash, invited_by,
                 _now(), expires_at))
        self.log_event(competition_id, "judge_invited",
                       {"role": role, "label": label})
        return iid

    def get_invitation_by_token_hash(self, token_hash: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM invitations WHERE token_hash=?",
                            (token_hash,)).fetchone()
        return dict(row) if row else None

    def list_invitations(self, competition_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM invitations WHERE competition_id=? "
                             "ORDER BY created_at", (competition_id,)).fetchall()
        return [dict(r) for r in rows]

    def accept_invitation(self, invitation_id: str, account_id: str) -> bool:
        """Mark an invitation accepted exactly once.

        The ``accepted_at IS NULL`` guard makes this atomic: two concurrent
        accepts race on the UPDATE and exactly one sees a changed row.
        """
        with self._conn() as c:
            cur = c.execute(
                "UPDATE invitations SET accepted_at=?, accepted_by=? "
                "WHERE id=? AND accepted_at IS NULL",
                (_now(), account_id, invitation_id))
            return cur.rowcount == 1

    def revoke_invitation(self, invitation_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE invitations SET revoked_at=? WHERE id=?",
                      (_now(), invitation_id))

    # -- audit -------------------------------------------------------------- #
    def log_event(self, cid: str, kind: str, payload: dict | None = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO audit_events(competition_id,kind,payload_json,created_at) "
                "VALUES(?,?,?,?)",
                (cid, kind, json.dumps(payload or {}, sort_keys=True), _now()))

    def list_events(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM audit_events WHERE competition_id=? ORDER BY id",
                             (cid,)).fetchall()
        out = []
        for r in rows:
            ev = dict(r)
            ev["payload_json"] = json.dumps(
                redact_event_payload(json.loads(ev["payload_json"] or "{}")),
                sort_keys=True)
            out.append(ev)
        return out
