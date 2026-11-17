#!/bin/sh
# Start the product video's local stack at the story clock in $WORK/offset.
#   ./up.sh            start (creates the database the first time)
#   ./up.sh --fresh    drop the database, files and emails, then start from nothing
set -e
STACK=$(cd "$(dirname "$0")" && pwd)
. "$STACK/env.sh"
cd "$REPO/backend"
"$STACK/down.sh" >/dev/null

if [ "$1" = "--fresh" ]; then
  rm -rf "$WORK/storage" "$WORK/mail" "$WORK/logs"/* "$WORK/takes" "$WORK"/state-*.json "$WORK"/passkeys-*.json "$WORK/staff.json"
  echo "${PV_START_OFFSET:-0}" > "$WORK/offset"
fi

# Certificates: a local CA the browser and the backend trust, and one server certificate for
# every host the front door plays. They are dated from 1 Jan 2026, because the story clock runs
# behind the real one: a certificate made today is not yet valid on the story's day 0.
old() { faketime '2026-01-01 00:00:00' "$@"; }
if ! grep -qs "notBefore=Jan  1" "$WORK/certs/dated" || ! grep -qs verify.local.test "$WORK/certs/ext.cnf"; then
  rm -rf "$WORK/certs"
  mkdir -p "$WORK/certs" && cd "$WORK/certs"
  old openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -keyout ca.key -out ca.crt \
    -subj "/CN=Finvest product video local CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=finvestug.com" 2>/dev/null
  printf 'subjectAltName=DNS:finvestug.com,DNS:www.finvestug.com,DNS:app.finvestug.com,DNS:admin.finvestug.com,DNS:api.finvestug.com,DNS:storage.local.test,DNS:resend.local.test,DNS:mail.local.test,DNS:verify.local.test\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\nbasicConstraints=critical,CA:FALSE\nauthorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n' > ext.cnf
  old openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 3650 -extfile ext.cnf -out server.crt 2>/dev/null
  openssl x509 -in server.crt -noout -startdate > dated
  openssl x509 -in ca.crt -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64 > spki.txt
  openssl x509 -in server.crt -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64 >> spki.txt
  cd "$REPO/backend"
fi
grep -q "verify.local.test" /etc/hosts || echo "127.0.0.1 storage.local.test resend.local.test mail.local.test verify.local.test" >> /etc/hosts

# PostgreSQL on the story clock.
pg_ctlcluster 16 main stop 2>/dev/null || true
pg_start "$(faketime_spec)"
su postgres -c "psql -q -c \"ALTER USER postgres PASSWORD 'local-admin'\"" >/dev/null
if [ "$1" = "--fresh" ]; then
  su postgres -c "psql -q -c 'DROP DATABASE IF EXISTS $DB'" >/dev/null
fi
if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='$DB'\"" | grep -q 1; then
  su postgres -c "psql -q -c 'CREATE DATABASE $DB'" >/dev/null
  FINVEST_MIGRATOR_PASSWORD=local-migrator FINVEST_APP_PASSWORD=local-app FINVEST_WORKER_PASSWORD=local-worker \
    PGPASSWORD=local-admin psql -q -h localhost -U postgres -d $DB -f db/init-roles.sql >/dev/null
fi

backend_env
run() { # name role command...
  name=$1; role=$2; shift 2
  FINVEST_DATABASE_URL=$(url_for "$role") LD_PRELOAD=$FAKETIME_LIB FAKETIME=$(faketime_spec) FAKETIME_DONT_FAKE_MONOTONIC=1 \
    nohup "$@" > "$WORK/logs/$name.log" 2>&1 &
  echo $! > "$WORK/logs/$name.pid"
}
FINVEST_DATABASE_URL=$(url_for migrator) LD_PRELOAD=$FAKETIME_LIB FAKETIME=$(faketime_spec) \
  uv run --locked python -m app.migrate_main > "$WORK/logs/migrate.log" 2>&1
run api app uv run --locked uvicorn app.api_main:app --host 127.0.0.1 --port 8000
run worker worker uv run --locked python -m app.worker_main
run scheduler worker uv run --locked python -m app.scheduler_main
nohup node "$REPO/apps/product-video/stack/front.mjs" "$WORK" "$REPO" > "$WORK/logs/front.log" 2>&1 &
echo $! > "$WORK/logs/front.pid"

for i in $(seq 1 60); do
  curl -sf http://127.0.0.1:8000/health/ready >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://127.0.0.1:8000/health/ready >/dev/null || { echo "API did not start; see $WORK/logs/api.log"; exit 1; }
echo "Stack up at story day $(offset) ($(su postgres -c "psql -tAc 'SELECT now()::date'"))"
