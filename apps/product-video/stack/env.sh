# Shared settings for the product video's local stack. Sourced by up.sh and the other scripts.
# Nothing here is a real secret: the keys are generated per work directory, on this machine.

REPO=${REPO:-$(cd "$(dirname "$0")/../../.." && pwd)}
WORK=${PV_WORK:-/tmp/finvest-product-video}
DB=finvest_video
FAKETIME_LIB=/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1
mkdir -p "$WORK/logs"

# The story's clock: a whole number of days added to the real date, kept in $WORK/offset.
# Every process (PostgreSQL, API, worker, scheduler) runs under libfaketime with it, and the
# browser is moved to the same time, so "three months later" really is three months later.
offset() { cat "$WORK/offset" 2>/dev/null || echo 0; }
faketime_spec() { o=$(offset); case "$o" in -*) echo "${o}d" ;; *) echo "+${o}d" ;; esac; }

if [ ! -f "$WORK/keys.env" ]; then
  key() { python3 -c "import secrets; print(secrets.token_urlsafe(32))"; }
  cat > "$WORK/keys.env" <<KEYS
FINVEST_IDENTITY_DATA_KEY=$(key)
FINVEST_EMAIL_HASH_KEY=$(key)
FINVEST_AUTHENTICATOR_KEY=$(key)
KEYS
fi

backend_env() {
  # No proxy: the backend must reach only this machine. HIBP is off for the same reason.
  unset HTTPS_PROXY HTTP_PROXY https_proxy http_proxy ALL_PROXY all_proxy
  # Trust only the local CA, so the backend can reach the stand-ins and nothing else.
  export SSL_CERT_FILE="$WORK/certs/ca.crt" REQUESTS_CA_BUNDLE="$WORK/certs/ca.crt"
  set -a
  . "$WORK/keys.env"
  set +a
  export FINVEST_DATABASE_SSL_MODE=disable FINVEST_LOG_FORMAT=console
  export FINVEST_PAYMENTS_PROVIDER=sandbox FINVEST_CARDS_PROVIDER=sandbox
  export FINVEST_CARDS_SANDBOX_RETURN_URL=https://app.finvestug.com/pay/card/return
  export FINVEST_IDENTITY_PROVIDER=development-stub FINVEST_SCREENING_PROVIDER=development-stub
  export FINVEST_SMS_BACKEND=local-console
  export FINVEST_EMAIL_BACKEND=resend FINVEST_EMAIL_FROM="Finvest <no-reply@finvestug.com>"
  export FINVEST_EMAIL_RESEND_API_URL=https://resend.local.test FINVEST_EMAIL_RESEND_API_KEY=local-only
  # Payout numbers have their network name read from the ioTec Verify stand-in (front.mjs).
  export FINVEST_PAYOUT_NAME_PROVIDER=iotec FINVEST_IOTEC_VERIFY_BASE_URL=https://verify.local.test \
    FINVEST_IOTEC_VERIFY_CLIENT_ID=local-only FINVEST_IOTEC_VERIFY_API_KEY=local-only
  export FINVEST_OBJECT_STORAGE_BACKEND=s3 FINVEST_OBJECT_STORAGE_BUCKET=finvest-video
  export FINVEST_OBJECT_STORAGE_ENDPOINT=https://storage.local.test FINVEST_OBJECT_STORAGE_REGION=lon1
  export FINVEST_OBJECT_STORAGE_ACCESS_KEY_ID=local FINVEST_OBJECT_STORAGE_SECRET_ACCESS_KEY=local
  export FINVEST_OBJECT_STORAGE_PATH_STYLE=true
  export FINVEST_CLIENT_ALLOWED_ORIGINS='["https://app.finvestug.com"]'
  export FINVEST_STAFF_ALLOWED_ORIGINS='["https://admin.finvestug.com"]'
  export FINVEST_CLIENT_PASSKEY_SITE=finvestug.com FINVEST_STAFF_PASSKEY_SITE=admin.finvestug.com
  export FINVEST_PUBLIC_SITE_ORIGIN=https://finvestug.com
  export FINVEST_BREACH_LIST_CHECK=false FINVEST_EARLY_WITHDRAWAL_OPEN=true
  export FINVEST_PASSWORD_MEMORY_KIB=19456 FINVEST_PASSWORD_TIME_COST=2
  # The browser stays signed in through long takes.
  export FINVEST_CLIENT_WEB_SESSION_IDLE_MINUTES=120 FINVEST_STAFF_SESSION_IDLE_MINUTES=240
}

url_for() {
  case "$1" in
    migrator) echo "postgresql+asyncpg://finvest_migrator:local-migrator@localhost:5432/$DB" ;;
    app) echo "postgresql+asyncpg://finvest_app:local-app@localhost:5432/$DB" ;;
    worker) echo "postgresql+asyncpg://finvest_worker:local-worker@localhost:5432/$DB" ;;
  esac
}

# PostgreSQL on the story clock. pg_ctlcluster drops the environment, so pg_ctl is called
# directly, with libfaketime in the server's own environment.
pg_start() {
  su postgres -c "LD_PRELOAD=$FAKETIME_LIB FAKETIME='$1' FAKETIME_DONT_FAKE_MONOTONIC=1 /usr/lib/postgresql/16/bin/pg_ctl -w -D /var/lib/postgresql/16/main -o '-c config_file=/etc/postgresql/16/main/postgresql.conf' -l /var/log/postgresql/postgresql-16-main.log start" >/dev/null
}
