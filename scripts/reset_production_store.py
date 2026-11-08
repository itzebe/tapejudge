"""Reset the deployed store to a known-good baseline: keep an allowlist, delete the rest.

This is the recovery tool for the case where the test suite or the journey probe
was pointed at a real Postgres store and filled it with fixture rows. It deletes
every competition and account **except** the ones named on the command line, so a
reset always converges on an explicit, reviewable baseline rather than "delete
whatever looks like junk".

Safety properties:

* It refuses to run unless the keep-list names at least one competition that
  actually exists, so an empty or mistyped keep-list cannot wipe the store.
* It prints the full deletion set and changes nothing without ``--confirm``.
* Identifiers are passed as bound parameters; only the schema and table names
  come from this file's own constants.

Usage::

    python3 scripts/reset_production_store.py \
      --keep-competition comp_563ebff5be18 \
      --keep-account organiser@demo.tapejudge.test \
      --keep-account judge@demo.tapejudge.test \
      --keep-account entrant@demo.tapejudge.test \
      --confirm
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge.store import _pg_schema

_DEPENDENT_TABLES = (
    "evaluations",
    "entries",
    "invitations",
    "memberships",
    "conflicts",
    "audit_events",
)


def partition(rows: list[dict], keep: set[str], key: str) -> tuple[list[dict], list[dict]]:
    """Split ``rows`` into (to_delete, to_keep) by ``key`` against ``keep`` (pure)."""
    to_delete = [r for r in rows if r.get(key) not in keep]
    to_keep = [r for r in rows if r.get(key) in keep]
    return to_delete, to_keep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep-competition", action="append", default=[],
                    help="competition id to keep (repeatable)")
    ap.add_argument("--keep-account", action="append", default=[],
                    help="account email to keep (repeatable)")
    ap.add_argument("--confirm", action="store_true",
                    help="perform the deletion; without it this is a dry run")
    ap.add_argument("--dsn", default=os.environ.get("TAPEJUDGE_DATABASE_URL"))
    ap.add_argument("--schema", default=None)
    args = ap.parse_args(argv)

    if not args.dsn:
        print("error: no DSN (set TAPEJUDGE_DATABASE_URL or pass --dsn)", file=sys.stderr)
        return 2
    if not args.keep_competition:
        print("error: --keep-competition is required (a reset with no baseline is a wipe)",
              file=sys.stderr)
        return 2

    schema = args.schema or _pg_schema(":memory:")
    keep_comps = set(args.keep_competition)
    keep_accounts = set(args.keep_account)

    import psycopg

    with psycopg.connect(args.dsn) as conn:
        comps = [{"id": r[0], "name": r[1]} for r in conn.execute(
            f'select id, name from "{schema}".competitions order by created_at').fetchall()]
        accounts = [{"id": r[0], "email": r[1]} for r in conn.execute(
            f'select id, email from "{schema}".accounts order by email').fetchall()]

        missing = keep_comps - {c["id"] for c in comps}
        if missing:
            print(f"error: keep competition(s) not found: {sorted(missing)}", file=sys.stderr)
            return 2

        del_comps, kept_comps = partition(comps, keep_comps, "id")
        del_accounts, kept_accounts = partition(accounts, keep_accounts, "email")

        print(f"schema {schema}")
        print(f"  keep competitions: {[c['id'] for c in kept_comps]}")
        print(f"  delete competitions: {len(del_comps)}")
        print(f"  keep accounts: {[a['email'] for a in kept_accounts]}")
        print(f"  delete accounts: {len(del_accounts)}")
        for a in del_accounts:
            print(f"    - {a['email']}")

        if not args.confirm:
            print("\ndry run — re-run with --confirm to apply")
            return 0

        comp_ids = [c["id"] for c in del_comps]
        acct_ids = [a["id"] for a in del_accounts]
        deleted: dict[str, int] = {}
        with conn.cursor() as cur:
            if comp_ids:
                ph = ",".join(["%s"] * len(comp_ids))
                for table in _DEPENDENT_TABLES:
                    cur.execute(
                        f'delete from "{schema}".{table} where competition_id in ({ph})', comp_ids)
                    deleted[table] = cur.rowcount
                cur.execute(f'delete from "{schema}".competitions where id in ({ph})', comp_ids)
                deleted["competitions"] = cur.rowcount
            if acct_ids:
                ph = ",".join(["%s"] * len(acct_ids))
                # memberships/invitations for kept competitions that reference a
                # deleted account would dangle; the demo accounts are kept, so in
                # practice this only clears fixture accounts.
                cur.execute(
                    f'delete from "{schema}".memberships where account_id in ({ph})', acct_ids)
                deleted["memberships_by_account"] = cur.rowcount
                cur.execute(f'delete from "{schema}".accounts where id in ({ph})', acct_ids)
                deleted["accounts"] = cur.rowcount
        conn.commit()

        print("\ndeleted:")
        for k, v in deleted.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
