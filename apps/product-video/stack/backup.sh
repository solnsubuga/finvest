#!/bin/sh
# Save the whole story state after a chapter: database, stored files, emails, the clock, and the
# browsers' sign-ins and passkeys, which belong with the sessions in that database.
#   ./backup.sh <name>     e.g. ./backup.sh after-03
set -e
STACK=$(cd "$(dirname "$0")" && pwd)
. "$STACK/env.sh"
name=${1:?usage: backup.sh <name>}
mkdir -p "$WORK/backups/$name"
su postgres -c "pg_dump -Fc $DB" > "$WORK/backups/$name/db.dump"
cd "$WORK"
tar -cf "$WORK/backups/$name/files.tar" offset keys.env $(ls -d storage mail state-*.json passkeys-*.json 2>/dev/null)
echo "Saved $name"
