"""Remove synthetic QA competitions from the deployed Postgres store.

The end-to-end probe (``scripts/journey_probe.mjs``) creates a real competition
every time it runs. Run against the deployed app it leaves that round behind, so
the public landing page fills with ``Journey Probe Round`` rows that are not
product data. This tool removes competitions by exact name, together with every
row that references them, in one transaction.

It is deliberately conservative:

* It targets an **exact** name, so a run for ``Journey Probe Round`` can never
  match the demo round or any other competition.
* It prints what it would delete and does nothing unless ``--confirm`` is given.
* It refuses to delete *every* competition, so the landing page is never emptied.

Postgres only: this operates on the deployed store, which is the one that
accumulates debris. Usage::

    # dry run — list what matches, change nothing
    python3 scripts/purge_demo_competitions.py --name "Journey Probe Round"

    # perform the deletion
    python3 scripts/purge_demo_competitions.py --name "Journey Probe Round" --confirm
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tapejudge.store import _pg_schema

# Every table that holds a row scoped to one competition. Order does not matter
# (there are no cross-table foreign keys), but keep it explicit and complete.
_DEPENDENT_TABLES = (
    "evaluations",
    "entries",
    "invitations",
    "memberships",
    "conflicts",
    "audit_events",
)


def select_by_name(competitions: list[dict], name: str) -> list[dict]:
    """The competitions whose name equals ``name`` exactly (pure, testable)."""
    return [c for c in competitions if c.get("name") == name]


def _guard_not_everything(matched: list[dict], total: int) -> None:
    if total and len(matched) >= total:
        raise SystemExit(
            f"refusing: '{matched[0]['name']}' is every competition in the store "
            f"({total}); that is a product data loss, not a cleanup.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="exact competition name to remove")
    ap.add_argument("--confirm", action="store_true",
                    help="perform the deletion; without it this is a dry run")
    ap.add_argument("--dsn", default=os.environ.get("TAPEJUDGE_DATABASE_URL"),
                    help="Postgres DSN (default: $TAPEJUDGE_DATABASE_URL)")
    ap.add_argument("--schema", default=None,
                    help="override the schema (default: the app's schema hash)")
    args = ap.parse_args(argv)

    if not args.dsn:
        print("error: no DSN (set TAPEJUDGE_DATABASE_URL or pass --dsn)", file=sys.stderr)
        return 2

    schema = args.schema or _pg_schema(":memory:")

    import psycopg

    with psycopg.connect(args.dsn) as conn:
        all_rows = conn.execute(
            f'select id, name from "{schema}".competitions order by created_at'
        ).fetchall()
        competitions = [{"id": r[0], "name": r[1]} for r in all_rows]
        matched = select_by_name(competitions, args.name)

        if not matched:
            print(f"no competition named {args.name!r} in schema {schema} — nothing to do")
            return 0

        _guard_not_everything(matched, len(competitions))

        print(f"schema {schema}: {len(matched)} of {len(competitions)} competitions match")
        for c in matched:
            print(f"  {c['id']}  {c['name']}")

        if not args.confirm:
            print("\ndry run — re-run with --confirm to delete these and their rows")
            return 0

        ids = [c["id"] for c in matched]
        placeholders = ",".join(["%s"] * len(ids))
        deleted: dict[str, int] = {}
        with conn.cursor() as cur:
            for table in _DEPENDENT_TABLES:
                cur.execute(
                    f'delete from "{schema}".{table} where competition_id in ({placeholders})',
                    ids)
                deleted[table] = cur.rowcount
            cur.execute(
                f'delete from "{schema}".competitions where id in ({placeholders})', ids)
            deleted["competitions"] = cur.rowcount
        conn.commit()

        print("\ndeleted:")
        for table, n in deleted.items():
            print(f"  {table}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
