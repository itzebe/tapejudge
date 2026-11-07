"""PostgreSQL persistence for TapeJudge.

This is the durable store for a hosted deployment: a serverless app instance has
no writable, persistent filesystem, so the SQLite file cannot be used there. This
adapter implements the *same* public interface as :class:`tapejudge.store.Store`
so the service, engine and web layers are unchanged and do not know which store
they hold. The SQLite store remains the default for local use and for the test
suite (``:memory:``); nothing here replaces it.

The two stores are kept honest by a shared behavioural contract test
(``tests/test_store_contract.py``), so a semantic drift between them is a test
failure, not a silent difference.

Design rules, identical to the SQLite store:

* A rubric is stored once, at freeze, with its digest and netlist hash; a frozen
  rubric cannot be replaced.
* Evaluations are append-only, guarded by a UNIQUE (competition, entry, judge)
  constraint that surfaces as :class:`DuplicateError`, never a raw driver error.
* ``audit_events`` is an append-only log, redacted at the read boundary.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row

from .store import SCHEMA_VERSION, DuplicateError, _now, new_id, redact_event_payload

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
    owner_account_id TEXT,
    opens_at      TEXT NOT NULL DEFAULT '',
    closes_at     TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'draft',
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
    submitter_account_id TEXT,
    submitted_at  TEXT NOT NULL,
    UNIQUE (competition_id, stable_id)
);
CREATE TABLE IF NOT EXISTS evaluations (
    id             TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    entry_id       TEXT NOT NULL REFERENCES entries(id),
    judge          TEXT NOT NULL,
    judge_account_id TEXT,
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
    role           TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    UNIQUE (competition_id, account_id, role)
);
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
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    competition_id TEXT NOT NULL,
    kind           TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_comp ON entries(competition_id);
CREATE INDEX IF NOT EXISTS idx_evals_comp ON evaluations(competition_id);
CREATE INDEX IF NOT EXISTS idx_audit_comp ON audit_events(competition_id, id);
"""

_ADDED_COLUMNS = {
    "competitions": {"owner_account_id": "TEXT"},
    "entries": {"submitter_account_id": "TEXT"},
    "evaluations": {"judge_account_id": "TEXT"},
}


class PostgresStore:
    """The durable, shared store. Same interface as :class:`Store`."""

    def __init__(self, dsn: str, *, schema: str | None = None):
        self.path = dsn
        self._lock = threading.RLock()
        self._schema = schema
        # One connection per store instance, serialized by an RLock: the SQLite
        # store does the same, and it keeps a single request's multi-statement
        # unit of work on one connection. Serverless gives each instance its own
        # process, so this is per-instance, not shared.
        self._conn_obj = psycopg.connect(dsn, row_factory=dict_row)
        if schema:
            # A dedicated schema gives each store instance its own tables on one
            # database, so tests (and any side-by-side use) never collide.
            with self._conn() as c:
                c.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                c.execute(f'SET search_path TO "{schema}"')
        self._init()

    @contextmanager
    def _conn(self):
        with self._lock:
            try:
                yield self._conn_obj
            except Exception:
                self._conn_obj.rollback()
                raise
            else:
                self._conn_obj.commit()

    def close(self) -> None:
        with self._lock:
            self._conn_obj.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(_SCHEMA)
            self._migrate(c)
            c.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', %s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (str(SCHEMA_VERSION),))

    def _migrate(self, c) -> None:
        for table, columns in _ADDED_COLUMNS.items():
            rows = c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s AND table_schema=current_schema()",
                (table,)).fetchall()
            existing = {r["column_name"] for r in rows}
            for name, decl in columns.items():
                if name not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def schema_version(self) -> int:
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
                "VALUES(%s,%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s)",
                (cid, name, description, organiser, opens_at, closes_at,
                 json.dumps(list(judges)), "[]", _now(), prize_note, owner_account_id))
        self.log_event(cid, "competition_created", {"name": name, "judges": list(judges)})
        return cid

    def get_competition(self, cid: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM competitions WHERE id=%s", (cid,)).fetchone()
        return dict(row) if row else None

    def list_competitions(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM competitions ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def set_rubric(self, cid: str, *, rubric_json, rubric_digest, netlist_hex,
                   netlist_sha256, n_in, n_out, gates, tie_break) -> None:
        with self._conn() as c:
            row = c.execute("SELECT status FROM competitions WHERE id=%s", (cid,)).fetchone()
            if row is None:
                raise KeyError(cid)
            if row["status"] != "draft":
                raise PermissionError(
                    "rubric is frozen; a published rubric cannot be replaced")
            c.execute(
                "UPDATE competitions SET rubric_json=%s, rubric_digest=%s, netlist_hex=%s, "
                "netlist_sha256=%s, n_in=%s, n_out=%s, gates=%s, tie_break_json=%s WHERE id=%s",
                (rubric_json, rubric_digest, netlist_hex, netlist_sha256,
                 n_in, n_out, gates, json.dumps(list(tie_break)), cid))

    def freeze(self, cid: str, commit_key: str) -> None:
        with self._conn() as c:
            row = c.execute("SELECT status, rubric_json FROM competitions WHERE id=%s",
                            (cid,)).fetchone()
            if row is None:
                raise KeyError(cid)
            if not row["rubric_json"]:
                raise ValueError("cannot freeze a competition with no rubric")
            if row["status"] != "draft":
                raise PermissionError("competition is already frozen")
            c.execute("UPDATE competitions SET status='frozen', commit_key=%s, frozen_at=%s "
                      "WHERE id=%s", (commit_key, _now(), cid))

    def set_judges(self, cid: str, judges: list[str]) -> None:
        with self._conn() as c:
            row = c.execute("SELECT status FROM competitions WHERE id=%s", (cid,)).fetchone()
            if row is None:
                raise KeyError(cid)
            if row["status"] != "draft":
                raise PermissionError("the panel is frozen and cannot be changed")
            c.execute("UPDATE competitions SET judges_json=%s WHERE id=%s",
                      (json.dumps(list(judges)), cid))

    def set_status(self, cid: str, status: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE competitions SET status=%s WHERE id=%s", (status, cid))

    def record_publication(self, cid: str, *, chain_id, processor, circuit_id, tx_hash,
                           mode, fee_wei=None) -> None:
        with self._conn() as c:
            c.execute("UPDATE competitions SET chain_id=%s, processor=%s, circuit_id=%s, "
                      "tx_hash=%s, published_at=%s, publish_mode=%s, fee_wei=%s WHERE id=%s",
                      (chain_id, processor, circuit_id, tx_hash, _now(), mode,
                       None if fee_wei is None else str(fee_wei), cid))

    def mark_settled(self, cid: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE competitions SET status='settled', settled_at=%s WHERE id=%s",
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
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (eid, cid, stable_id, title, summary, link,
                     json.dumps(evidence or {}), content_hash, 1 if private else 0, _now(),
                     submitter_account_id))
            except pg_errors.UniqueViolation as e:
                raise DuplicateError(
                    f"entry {stable_id!r} already exists in this competition") from e
        self.log_event(cid, "entry_submitted", {"entry": stable_id, "content_hash": content_hash})
        return eid

    def list_entries(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM entries WHERE competition_id=%s "
                             "ORDER BY submitted_at", (cid,)).fetchall()
        return [dict(r) for r in rows]

    def get_entry_by_stable(self, cid: str, stable_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM entries WHERE competition_id=%s AND stable_id=%s",
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
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (eid, cid, entry_id, judge, json.dumps(scores, sort_keys=True),
                     justification, commitment, nonce, _now(), judge_account_id))
            except pg_errors.UniqueViolation as e:
                raise DuplicateError(
                    f"{judge!r} has already scored entry {entry_id!r}") from e
        self.log_event(cid, "evaluation_committed",
                       {"entry_id": entry_id, "judge": judge, "commitment": commitment})
        return eid

    def list_evaluations(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM evaluations WHERE competition_id=%s "
                             "ORDER BY created_at", (cid,)).fetchall()
        return [dict(r) for r in rows]

    def has_evaluation(self, cid: str, entry_id: str, judge: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM evaluations WHERE competition_id=%s AND entry_id=%s "
                            "AND judge=%s", (cid, entry_id, judge)).fetchone()
        return row is not None

    # -- conflicts ---------------------------------------------------------- #
    def declare_conflict(self, cid: str, *, judge, entry_id, reason) -> str:
        fid = new_id("coi")
        with self._conn() as c:
            c.execute("INSERT INTO conflicts(id,competition_id,judge,entry_id,"
                      "reason,declared_at) VALUES(%s,%s,%s,%s,%s,%s) "
                      "ON CONFLICT (competition_id, judge, entry_id) DO UPDATE SET "
                      "reason=EXCLUDED.reason, declared_at=EXCLUDED.declared_at",
                      (fid, cid, judge, entry_id, reason, _now()))
        self.log_event(cid, "conflict_declared",
                       {"judge": judge, "entry_id": entry_id, "reason": reason})
        return fid

    def list_conflicts(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM conflicts WHERE competition_id=%s", (cid,)).fetchall()
        return [dict(r) for r in rows]

    # -- accounts ----------------------------------------------------------- #
    def create_account(self, *, email, display_name, password_hash) -> str:
        aid = new_id("acct")
        with self._conn() as c:
            try:
                c.execute(
                    "INSERT INTO accounts(id,email,display_name,password_hash,"
                    "session_epoch,created_at) VALUES(%s,%s,%s,%s,0,%s)",
                    (aid, email, display_name, password_hash, _now()))
            except pg_errors.UniqueViolation as e:
                raise DuplicateError("an account with that email already exists") from e
        return aid

    def get_account(self, aid: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM accounts WHERE id=%s", (aid,)).fetchone()
        return dict(row) if row else None

    def get_account_by_email(self, email: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM accounts WHERE email=%s", (email,)).fetchone()
        return dict(row) if row else None

    def set_password_hash(self, aid: str, password_hash: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE accounts SET password_hash=%s WHERE id=%s", (password_hash, aid))

    def bump_session_epoch(self, aid: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE accounts SET session_epoch=session_epoch+1 WHERE id=%s", (aid,))

    # -- memberships -------------------------------------------------------- #
    def add_membership(self, *, account_id, competition_id, role, label) -> str:
        mid = new_id("mem")
        with self._conn() as c:
            try:
                c.execute(
                    "INSERT INTO memberships(id,account_id,competition_id,role,label,created_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s)",
                    (mid, account_id, competition_id, role, label, _now()))
            except pg_errors.UniqueViolation as e:
                raise DuplicateError(
                    "that account already has this role here, or the label is taken") from e
        return mid

    def list_memberships(self, competition_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM memberships WHERE competition_id=%s ORDER BY created_at",
                (competition_id,)).fetchall()
        return [dict(r) for r in rows]

    def memberships_for_account(self, account_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM memberships WHERE account_id=%s ORDER BY created_at",
                (account_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_membership(self, competition_id: str, account_id: str, role: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM memberships WHERE competition_id=%s AND account_id=%s AND role=%s",
                (competition_id, account_id, role)).fetchone()
        return dict(row) if row else None

    def find_membership_by_label(self, competition_id: str, label: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM memberships WHERE competition_id=%s AND label=%s",
                (competition_id, label)).fetchone()
        return dict(row) if row else None

    def remove_membership(self, membership_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM memberships WHERE id=%s", (membership_id,))

    # -- invitations -------------------------------------------------------- #
    def create_invitation(self, *, competition_id, email, role, label, token_hash,
                          expires_at, invited_by=None) -> str:
        iid = new_id("inv")
        with self._conn() as c:
            c.execute(
                "INSERT INTO invitations(id,competition_id,email,role,label,token_hash,"
                "invited_by,created_at,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (iid, competition_id, email, role, label, token_hash, invited_by,
                 _now(), expires_at))
        self.log_event(competition_id, "judge_invited",
                       {"role": role, "label": label})
        return iid

    def get_invitation_by_token_hash(self, token_hash: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM invitations WHERE token_hash=%s",
                            (token_hash,)).fetchone()
        return dict(row) if row else None

    def list_invitations(self, competition_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM invitations WHERE competition_id=%s "
                             "ORDER BY created_at", (competition_id,)).fetchall()
        return [dict(r) for r in rows]

    def accept_invitation(self, invitation_id: str, account_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE invitations SET accepted_at=%s, accepted_by=%s "
                "WHERE id=%s AND accepted_at IS NULL",
                (_now(), account_id, invitation_id))
            return cur.rowcount == 1

    def revoke_invitation(self, invitation_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE invitations SET revoked_at=%s WHERE id=%s",
                      (_now(), invitation_id))

    # -- audit -------------------------------------------------------------- #
    def log_event(self, cid: str, kind: str, payload: dict | None = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO audit_events(competition_id,kind,payload_json,created_at) "
                "VALUES(%s,%s,%s,%s)",
                (cid, kind, json.dumps(payload or {}, sort_keys=True), _now()))

    def list_events(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM audit_events WHERE competition_id=%s ORDER BY id",
                             (cid,)).fetchall()
        out = []
        for r in rows:
            ev = dict(r)
            ev["payload_json"] = json.dumps(
                redact_event_payload(json.loads(ev["payload_json"] or "{}")),
                sort_keys=True)
            out.append(ev)
        return out
