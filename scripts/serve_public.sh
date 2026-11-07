#!/bin/sh
# Public-launch launcher for a single-instance host (a VM, a container, or the
# sandbox runtime that maps a port to a public HTTPS URL).
#
# It enforces the same invariants the container entrypoint does, then runs
# gunicorn with one worker (SQLite is single-writer). The database lives on a
# persistent path outside the repository, so it is never committed and it
# survives a restart.
#
#   TAPEJUDGE_DATA_DIR  persistent directory for the database (default /var/lib/tapejudge)
#   TAPEJUDGE_SECRET    Flask signing secret; if unset it is read from
#                       "$TAPEJUDGE_DATA_DIR/secret" (created once, mode 600)
#   TAPEJUDGE_HOST      bind address (default 0.0.0.0)
#   TAPEJUDGE_PORT      bind port (default 8080)
#   TAPEJUDGE_DATABASE_URL  if set, use the durable Postgres store instead of SQLite
set -eu

DATA_DIR="${TAPEJUDGE_DATA_DIR:-/var/lib/tapejudge}"
mkdir -p "$DATA_DIR"
SECRET_FILE="$DATA_DIR/secret"

if [ -n "${TAPEJUDGE_ALLOW_DEV_SECRET:-}" ]; then
    echo "refusing to start: TAPEJUDGE_ALLOW_DEV_SECRET is set" >&2
    exit 1
fi

if [ -z "${TAPEJUDGE_SECRET:-}" ]; then
    if [ -f "$SECRET_FILE" ]; then
        TAPEJUDGE_SECRET="$(cat "$SECRET_FILE")"
    else
        TAPEJUDGE_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
        ( umask 077; printf '%s' "$TAPEJUDGE_SECRET" > "$SECRET_FILE" )
    fi
    export TAPEJUDGE_SECRET
fi

export TAPEJUDGE_DB="${TAPEJUDGE_DB:-$DATA_DIR/tapejudge.db}"
export TAPEJUDGE_HOST="${TAPEJUDGE_HOST:-0.0.0.0}"
export TAPEJUDGE_PORT="${TAPEJUDGE_PORT:-8080}"

# Seed the SYNTHETIC demonstration once, so a first visitor sees the workflow.
if [ -z "${TAPEJUDGE_DATABASE_URL:-}" ] && [ ! -f "$TAPEJUDGE_DB" ]; then
    echo "seeding synthetic demo into $TAPEJUDGE_DB"
    python3 -m tapejudge.seed --journey "$TAPEJUDGE_DB" >/dev/null
fi

exec python3 -m gunicorn \
    --workers "${TAPEJUDGE_WORKERS:-1}" \
    --bind "$TAPEJUDGE_HOST:$TAPEJUDGE_PORT" \
    --access-logfile - \
    --error-logfile - \
    --timeout 30 \
    "tapejudge.web.app:app"
