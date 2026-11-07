#!/bin/sh
# Container entrypoint: refuse an insecure start, then run gunicorn.
#
# The application itself refuses to start in production without a strong secret
# (see tapejudge/web/app.py). This script adds the container-level check that the
# dev-only escape hatches are not set, then hands off to gunicorn with the worker
# count the store can support.
set -eu

if [ -n "${TAPEJUDGE_ALLOW_DEV_SECRET:-}" ]; then
    echo "refusing to start: TAPEJUDGE_ALLOW_DEV_SECRET is set" >&2
    exit 1
fi
if [ -z "${TAPEJUDGE_SECRET:-}" ]; then
    echo "refusing to start: TAPEJUDGE_SECRET is not set" >&2
    exit 1
fi

# SQLite is a single-writer store; the honest default is one worker. An operator
# who has moved to a shared store may raise TAPEJUDGE_WORKERS.
exec gunicorn \
    --workers "${TAPEJUDGE_WORKERS:-1}" \
    --bind "${TAPEJUDGE_HOST:-0.0.0.0}:${TAPEJUDGE_PORT:-8080}" \
    --access-logfile - \
    --error-logfile - \
    --timeout 30 \
    "tapejudge.web.app:app"
