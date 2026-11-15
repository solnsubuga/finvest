#!/bin/sh
# Stop the product video's local stack. Data stays in PostgreSQL and $WORK.
STACK=$(cd "$(dirname "$0")" && pwd)
. "$STACK/env.sh"
for p in api worker scheduler front; do
  [ -f "$WORK/logs/$p.pid" ] && kill "$(cat "$WORK/logs/$p.pid")" 2>/dev/null
  rm -f "$WORK/logs/$p.pid"
done
pkill -f "uvicorn app.api_main" 2>/dev/null; pkill -f "app.worker_main" 2>/dev/null; pkill -f "app.scheduler_main" 2>/dev/null
pkill -f "stack/front.mjs" 2>/dev/null
sleep 2
# Nothing of the old clock may survive: a second worker or an old API on the old clock would
# answer instead of the new one.
pattern="uvicorn app.api_main|app.worker_main|app.scheduler_main|stack/front.mjs"
for i in 1 2 3 4 5 6 7 8 9 10; do
  pgrep -f "$pattern" >/dev/null || break
  pkill -9 -f "$pattern" 2>/dev/null
  sleep 1
done
if pgrep -f "$pattern" >/dev/null; then echo "down.sh: processes survived" >&2; exit 1; fi
pg_ctlcluster 16 main stop 2>/dev/null
true
