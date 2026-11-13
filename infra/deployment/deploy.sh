#!/usr/bin/env bash
# Runs on the host from an immutable releases/<id> directory, ported from Fidiba's deploy.sh.
#
# Each release runs as a colour, blue or green (compose.yaml). The new colour starts beside the one
# serving now, migrations run while that one still serves, then the long-lived Caddy switches to
# the new colour and the old one stops, so requests never go unanswered. A failure before the
# switch leaves the previous colour serving; a failure of the public checks after it switches
# back.
#
#   bash deploy.sh [force_recreate: true|false]
#
# Migrations must be safe to run beside the previous release (expand, then contract in a later
# release). release.env may set FINVEST_STOP_BEFORE_MIGRATE=true for a release that cannot,
# which stops the previous colour first and serves "being updated" until the new one is up.
set -euo pipefail
umask 077

release=$(pwd -P)
root=$(dirname "$(dirname "$release")")
[[ $(basename "$(dirname "$release")") == releases ]] || { echo 'Run from releases/<id>' >&2; exit 1; }
force=${1:-false}
[[ $force == true || $force == false ]] || { echo 'Expected true or false' >&2; exit 1; }
exec 9>"$root/deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }

value() { sed -n "s/^$1=//p" release.env; }
project=$(value FINVEST_DEPLOY_PROJECT)
api_domain=$(value FINVEST_API_DOMAIN)
admin_domain=$(value FINVEST_ADMIN_DOMAIN)
# Set only when this release serves the public website (prepare_release.py).
site_domain=$(value FINVEST_SITE_DOMAIN)
edge_subnet=$(value FINVEST_EDGE_SUBNET)
stop_before_migrate=$(value FINVEST_STOP_BEFORE_MIGRATE)
# A rehearsal on a machine without Cloudflare in front checks the local Caddy directly.
check_local=$(value FINVEST_CHECK_LOCAL)
[[ $project =~ ^[a-z][a-z0-9-]*$ ]] || exit 1
[[ $api_domain =~ ^[a-z0-9][a-z0-9.-]*$ && $admin_domain =~ ^[a-z0-9][a-z0-9.-]*$ ]] || exit 1
[[ -z $site_domain || $site_domain =~ ^[a-z0-9][a-z0-9.-]*$ ]] || exit 1
[[ $edge_subnet =~ ^[0-9./]+$ ]] || exit 1

# Every process must be told which environment this is, and it must be this project's. Payments,
# identity checks and SMS refuse their sandbox and stub providers in production by that value, so
# a release that leaves it out or names another environment is refused before anything starts.
environment=${project#finvest-}
[[ $environment == staging || $environment == production ]] || { echo 'Not a Finvest environment' >&2; exit 1; }
for service in migrate api worker scheduler; do
  if [[ $(sed -n 's/^FINVEST_ENVIRONMENT=//p' "runtime/$service.env") != "$environment" ]]; then
    echo "runtime/$service.env does not say FINVEST_ENVIRONMENT=$environment" >&2
    exit 1
  fi
done

compose=(docker compose --env-file release.env -f compose.yaml)
"${compose[@]}" config --quiet
if ! docker network inspect "$project-edge" >/dev/null 2>&1; then
  docker network create --subnet "$edge_subnet" "$project-edge" >/dev/null
fi

active=$(cat "$root/colour" 2>/dev/null || true)
case $active in
  blue) next=green ;;
  green | '') next=blue ;;
  *) echo "Unexpected colour in $root/colour" >&2; exit 1 ;;
esac
colour() { FINVEST_COLOUR=$1 "${compose[@]}" -p "$project-$1" "${@:2}"; }
edge() { FINVEST_EDGE_DIR=$1 FINVEST_TLS_DIR=$root/tls "${compose[@]}" -p "$project" --profile edge "${@:2}"; }
stop_previous() {
  if [[ -n $active ]]; then
    colour "$active" down --remove-orphans
  fi
}

# Every image is pinned by digest, so one already on the host is the same image.
colour "$next" --profile tools --profile edge pull --quiet --policy missing
# The origin certificate lives outside any release, like Caddy itself.
mkdir -p "$root/tls"
cp tls/origin.pem tls/origin.key "$root/tls/"
chmod 0600 "$root/tls/origin.key"
# Caddy's next configuration: this release's files, sending traffic to the new colour.
stage="$root/edge.next"
rm -rf -- "$stage"
cp -R edge "$stage"
sed -i -E "s/(api|console|site)-blue:/\1-$next:/" "$stage/upstreams.caddy"
chmod 0755 "$stage"
chmod 0644 "$stage"/*
edge "$stage" run --rm --no-deps -T caddy caddy validate --config /etc/caddy/Caddyfile
# Refuse a release whose database roles are outside their boundary.
for pair in 'migrate finvest_migrator' 'api finvest_app' 'worker finvest_worker'; do
  read -r service role <<< "$pair"
  colour "$next" run --rm --no-deps -T "$service" python -m app.infrastructure.database.roles "$role"
done

state=starting
restore() {
  status=$?
  [[ $status != 0 ]] || return 0
  if [[ $state == switched && -d $root/edge.previous ]]; then
    echo 'Public checks failed; switching back to the previous release.' >&2
    cp "$root/edge.previous"/* "$root/edge"/ || true
    edge "$root/edge" exec -T caddy caddy reload --config /etc/caddy/Caddyfile || true
  fi
  if [[ $state == started || $state == switched ]]; then
    colour "$next" down --remove-orphans || true
  fi
  exit "$status"
}
trap restore EXIT
# A failed earlier deployment may have left the new colour's containers behind.
colour "$next" down --remove-orphans
if [[ $stop_before_migrate == true ]]; then
  echo 'Stopping the previous release for a migration it cannot run beside.'
  stop_previous
fi
colour "$next" run --rm --no-deps -T migrate
state=started
options=(up -d --wait --wait-timeout 180)
if [[ $force == true ]]; then options+=(--force-recreate); fi
colour "$next" "${options[@]}" api worker scheduler console site

# Keep what to switch back to.
rm -rf -- "$root/edge.previous"
if [[ -n $active ]]; then
  cp -R "$root/edge" "$root/edge.previous"
fi
# Switch: Caddy reads its files from one directory outside any release, updated in place (the
# container keeps the directory it mounted), and reloads without dropping connections.
state=switched
mkdir -p "$root/edge"
chmod 0755 "$root/edge"
cp "$stage"/* "$root/edge"/
rm -rf -- "$stage"
edge "$root/edge" up -d caddy
edge "$root/edge" exec -T caddy caddy reload --config /etc/caddy/Caddyfile

# Check the public path end to end before recording success: Cloudflare, TLS, Caddy, the new
# colour and its database.
check() {
  local host=$1 path=$2 extra=()
  if [[ $check_local == true ]]; then
    extra=(--insecure --noproxy '*' --resolve "$host:${FINVEST_CHECK_PORT:-443}:127.0.0.1")
  fi
  curl --fail --silent --show-error --retry 10 --retry-delay 3 --retry-all-errors \
    --connect-timeout 5 --max-time 10 "${extra[@]}" \
    "https://$host:${FINVEST_CHECK_PORT:-443}$path" >/dev/null
}
check "$api_domain" /health/ready
check "$admin_domain" /
if [[ -n $site_domain ]]; then
  check "$site_domain" /
fi
state=finished
# Record the new colour first: the next deployment must never start over the one serving.
printf '%s\n' "$next" > "$root/colour"
if [[ -L $root/current && $(readlink "$root/current") != "$release" ]]; then
  ln -sfn "$(readlink "$root/current")" "$root/previous"
fi
ln -sfn "$release" "$root/current"
# In-flight requests finish: each API container drains before it exits (stop_grace_period).
stop_previous
# Keep the five newest releases.
find "$root/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | tail -n +6 |
  cut -d' ' -f2- | while read -r old; do
    [[ $old == "$(readlink "$root/current")" ]] || rm -rf -- "$old"
  done
echo "Deployment healthy; $next is serving."
