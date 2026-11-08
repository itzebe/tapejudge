#!/bin/sh
# Keep-alive supervisor for the single-instance public deployment.
#
# The work-host runtime has no service manager (no systemd, no supervisor), so a
# process that exits stays down until it is started again. This wrapper keeps the
# application up for as long as the sandbox lives: it runs the launcher, and if
# the launcher exits it waits briefly and runs it again. It does NOT survive the
# sandbox being torn down — nothing in the sandbox can; that is the documented
# limitation and the reason a permanent host (or a managed Postgres DSN) is still
# required for a durable deployment.
#
# Usage:
#   TAPEJUDGE_DATA_DIR=/workspace/.tapejudge-data TAPEJUDGE_PORT=12000 \
#     nohup sh scripts/keepalive.sh > /tmp/tapejudge-keepalive.log 2>&1 &
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

while true; do
    date -u '+%Y-%m-%dT%H:%M:%SZ keepalive: starting launcher'
    sh "$HERE/serve_public.sh" || date -u '+%Y-%m-%dT%H:%M:%SZ keepalive: launcher exited'
    date -u '+%Y-%m-%dT%H:%M:%SZ keepalive: restarting in 3s'
    sleep 3
done
