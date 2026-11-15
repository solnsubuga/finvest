#!/bin/sh
# Put the story back to a saved point, then start the stack there.
#   ./restore.sh <name>
set -e
STACK=$(cd "$(dirname "$0")" && pwd)
. "$STACK/env.sh"
name=${1:?usage: restore.sh <name>}
[ -f "$WORK/backups/$name/db.dump" ] || { echo "No backup $name"; exit 1; }
"$STACK/down.sh" >/dev/null
rm -rf "$WORK/storage" "$WORK/mail"
# Backups made since sign-ins were saved with them carry their own; older ones keep today's.
if tar -tf "$WORK/backups/$name/files.tar" | grep -q '^state-'; then rm -f "$WORK"/state-*.json "$WORK"/passkeys-*.json; fi
tar -C "$WORK" -xf "$WORK/backups/$name/files.tar"
pg_start "$(faketime_spec)"
su postgres -c "psql -q -c 'DROP DATABASE IF EXISTS $DB'" >/dev/null
su postgres -c "psql -q -c 'CREATE DATABASE $DB'" >/dev/null
su postgres -c "pg_restore -d $DB" < "$WORK/backups/$name/db.dump"
pg_ctlcluster 16 main stop
"$STACK/up.sh"
