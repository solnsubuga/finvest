#!/usr/bin/env bash
# Rehearses the deployment on one machine with Docker, with no cloud access: a throwaway registry
# and PostgreSQL, the real images, create-roles.sh, prepare_release.py and deploy.sh. It deploys
# twice (blue, then green) while polling the API, then makes the public check fail and expects the
# switch back. CI runs it on every infrastructure pull request.
#
#   bash infra/deployment/rehearse.sh
#
# Set BACKEND_IMAGE and CONSOLE_IMAGE (name@sha256:...) to reuse images already in a registry.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
work=$(mktemp -d)
host=api-staging.finvestug.com
port=18443
curl_local=(curl --silent --insecure --noproxy '*' --max-time 3 --resolve "$host:$port:127.0.0.1")

cleanup() {
  for colour in blue green; do
    docker compose -p "finvest-staging-$colour" down --remove-orphans >/dev/null 2>&1 || true
  done
  docker rm -f finvest-staging-caddy-1 finvest-rehearsal-db finvest-rehearsal-registry >/dev/null 2>&1 || true
  docker network rm finvest-staging-edge >/dev/null 2>&1 || true
  docker volume rm finvest-staging_caddy_data finvest-staging_caddy_config >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT
cleanup
mkdir -p "$work"

gateway=$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')
docker run -d --name finvest-rehearsal-db -e POSTGRES_PASSWORD=admin -e POSTGRES_DB=finvest \
  -p 15432:5432 postgres:17.12@sha256:dbb9623d5121b5d2c2c9e2fba56e40193cbb3510d4af7f035b1ab1823aff3205 >/dev/null

if [[ -z ${BACKEND_IMAGE:-} || -z ${CONSOLE_IMAGE:-} ]]; then
  docker run -d --name finvest-rehearsal-registry -p 127.0.0.1:15000:5000 registry:2 >/dev/null
  docker build --quiet -t localhost:15000/finvest-backend:rehearsal "$repo/backend"
  docker build --quiet -t localhost:15000/finvest-console:rehearsal --build-arg VITE_DEPLOY_ENV=staging \
    -f "$here/console.Dockerfile" "$repo"
  for name in backend console; do
    docker push --quiet "localhost:15000/finvest-$name:rehearsal" >/dev/null
    digest=$(curl --silent --noproxy '*' --head \
      -H 'Accept: application/vnd.oci.image.index.v1+json' \
      -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
      -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
      "http://localhost:15000/v2/finvest-$name/manifests/rehearsal" |
      tr -d '\r' | sed -n 's/^[Dd]ocker-[Cc]ontent-[Dd]igest: //p')
    [[ $digest == sha256:* ]]
    declare "${name^^}_IMAGE=localhost:15000/finvest-$name@$digest"
  done
fi

printf '%s\n' "ADMIN_URI=postgresql://postgres:admin@$gateway:15432/finvest" \
  'FINVEST_MIGRATOR_PASSWORD=mig/p@ss' 'FINVEST_APP_PASSWORD=app-pass' 'FINVEST_WORKER_PASSWORD=worker-pass' |
  bash "$here/create-roles.sh" "$repo/backend/db/init-roles.sql"

openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 2 -subj "/CN=$host" \
  -keyout "$work/key.pem" -out "$work/cert.pem" 2>/dev/null

# deploy NAME CHECK_PORT: prepare a release and run its deploy.sh.
deploy() {
  local release="$work/opt-finvest/releases/$1"
  mkdir -p "$(dirname "$release")"
  DEPLOY_ENVIRONMENT=staging API_DOMAIN=$host ADMIN_DOMAIN=admin-staging.finvestug.com \
    APP_DOMAIN=app-staging.finvestug.com SITE_DOMAINS=staging.finvestug.com \
    BACKEND_IMAGE=$BACKEND_IMAGE CONSOLE_IMAGE=$CONSOLE_IMAGE \
    DATABASE_HOST=$gateway DATABASE_PORT=15432 DATABASE_NAME=finvest \
    DATABASE_MIGRATOR_PASSWORD='mig/p@ss' DATABASE_APP_PASSWORD=app-pass DATABASE_WORKER_PASSWORD=worker-pass \
    ORIGIN_TLS_CERT=$(cat "$work/cert.pem") ORIGIN_TLS_KEY=$(cat "$work/key.pem") \
    FINVEST_REHEARSAL=true FINVEST_HTTP_PORT=18080 FINVEST_HTTPS_PORT=$port FINVEST_BIND_ADDRESS=127.0.0.1 \
    python3 "$here/prepare_release.py" "$release" >/dev/null
  (cd "$release" && FINVEST_CHECK_PORT=$2 bash deploy.sh false)
}
serving() { cat "$work/opt-finvest/colour"; }
ready() { "${curl_local[@]}" --output /dev/null --write-out '%{http_code}' "https://$host:$port/health/ready" || true; }

echo '== First deployment'
deploy first "$port"
[[ $(serving) == blue && $(ready) == 200 ]]

echo '== Second deployment, polling the API throughout'
( while :; do ready; echo; sleep 0.2; done > "$work/codes" ) &
poller=$!
deploy second "$port"
sleep 3
kill "$poller"
wait "$poller" 2>/dev/null || true
[[ $(serving) == green ]]
sort "$work/codes" | uniq -c
if grep -qv '^200$' "$work/codes"; then
  echo 'A request failed during the switch' >&2
  exit 1
fi

echo '== Third deployment, with a public check that fails'
if deploy third 9 > "$work/third.log" 2>&1; then
  echo 'A deployment whose public check fails must not succeed' >&2
  exit 1
fi
grep -q 'switching back to the previous release' "$work/third.log"
[[ $(serving) == green && $(ready) == 200 ]]
[[ -z $(docker ps -q --filter name=finvest-staging-blue) ]]

echo '== Edge'
code() { "${curl_local[@]}" --resolve "$1:$port:127.0.0.1" --output /dev/null --write-out '%{http_code}' "https://$1:$port$2" || true; }
[[ $(code admin-staging.finvestug.com /clients/any-route) == 200 ]]
[[ $(code staging.finvestug.com /) == 503 ]]
[[ $(code app-staging.finvestug.com /) == 503 ]]
[[ $(code "$host" /.env) == 404 ]]
[[ $(code unknown.example /) == 000 ]]
echo 'Rehearsal passed.'
