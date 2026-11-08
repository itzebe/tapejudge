#!/usr/bin/env sh
# Best-effort auto-start for the TapeJudge public demo.
#
# The work-host runtime does not survive a session ending: after a reset the
# app is gone until something starts it again. There is no service manager
# (PID 1 is `openhands-agent`), so this is invoked from ~/.bashrc — idempotent,
# non-blocking, and a no-op when the app is already listening.
#
# It is a mitigation, not a durable host. See docs/TAPEJUDGE_PRODUCTION_DEPLOYMENT_PLAN.md
# for the managed-hosting path that removes the reset window entirely.

set -eu

REPO=/workspace/tapejudge
DATA=/workspace/.tapejudge-data
PORT=12000
LOG="$DATA/autostart.log"

mkdir -p "$DATA"

# Already serving? Nothing to do.
if curl -s -o /dev/null -m 3 "http://127.0.0.1:$PORT/api/health"; then
  exit 0
fi

cd "$REPO"
{
  echo "[autostart] $(date -u +%FT%TZ) starting launcher"
  TAPEJUDGE_DATA_DIR="$DATA" TAPEJUDGE_HOST=0.0.0.0 TAPEJUDGE_PORT="$PORT" \
    sh scripts/serve_public.sh
} >>"$LOG" 2>&1
