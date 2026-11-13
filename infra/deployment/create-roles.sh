#!/usr/bin/env bash
# Runs on the application host, inside the VPC, during a Provision run. Creates the finvest_migrator,
# finvest_app and finvest_worker roles with backend/db/init-roles.sql as the database administrator.
# Reads ADMIN_URI and FINVEST_{MIGRATOR,APP,WORKER}_PASSWORD from standard input as NAME=value
# lines, so no secret appears on a command line, and the SQL from the file given as $1.
set -euo pipefail
umask 077
sql=${1:?Usage: create-roles.sh INIT_ROLES_SQL}
while IFS='=' read -r name value; do
  case $name in
    ADMIN_URI | FINVEST_MIGRATOR_PASSWORD | FINVEST_APP_PASSWORD | FINVEST_WORKER_PASSWORD)
      export "$name=$value" ;;
    '') ;;
    *) echo "Unexpected setting $name" >&2; exit 1 ;;
  esac
done
for name in ADMIN_URI FINVEST_MIGRATOR_PASSWORD FINVEST_APP_PASSWORD FINVEST_WORKER_PASSWORD; do
  [[ -n ${!name:-} ]] || { echo "Missing $name" >&2; exit 1; }
done
# psql 17 from Docker. Values pass by name (-e NAME), so none appears in the process list.
# Idempotent: creates missing roles and never changes an existing password. A new cluster can take
# a minute to accept connections.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if docker run --rm -i -e ADMIN_URI -e FINVEST_MIGRATOR_PASSWORD -e FINVEST_APP_PASSWORD \
    -e FINVEST_WORKER_PASSWORD postgres:17.12@sha256:dbb9623d5121b5d2c2c9e2fba56e40193cbb3510d4af7f035b1ab1823aff3205 \
    sh -c 'exec psql "$ADMIN_URI" --no-psqlrc --quiet --set ON_ERROR_STOP=1 --file -' < "$sql"; then
    echo 'Database roles are in place.'
    exit 0
  fi
  sleep 10
done
echo 'Could not apply init-roles.sql' >&2
exit 1
