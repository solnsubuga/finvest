#!/usr/bin/env bash
# shellcheck disable=SC2029
# (Remote commands are built from the values validated below and are meant to expand here.)
#
# Runs on the GitHub runner, ported from Fidiba's scripts/ship_deployment.py. Copies one prepared
# release to the host over SSH and runs its deploy.sh there.
#
#   ship-release.sh BUNDLE_DIR
#
# Reads DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY, REVISION, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT,
# REGISTRY_USER, REGISTRY_TOKEN and FORCE_RECREATE from the environment.
set -euo pipefail
umask 077
bundle=${1:?Usage: ship-release.sh BUNDLE_DIR}
[[ -f $bundle/deploy.sh ]] || { echo 'Not a release bundle' >&2; exit 1; }
[[ $DEPLOY_HOST =~ ^[0-9]+(\.[0-9]+){3}$ ]] || { echo 'DEPLOY_HOST is not valid' >&2; exit 1; }
[[ $DEPLOY_USER =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'DEPLOY_USER is not valid' >&2; exit 1; }
[[ $REVISION =~ ^[a-f0-9]{40}$ ]] || { echo 'REVISION is not valid' >&2; exit 1; }
[[ $GITHUB_RUN_ID =~ ^[0-9]+$ && $GITHUB_RUN_ATTEMPT =~ ^[0-9]+$ ]] || exit 1
[[ $REGISTRY_USER =~ ^[A-Za-z0-9_-]+(\[bot\])?$ ]] || { echo 'REGISTRY_USER is not valid' >&2; exit 1; }
force=${FORCE_RECREATE:-false}
[[ $force == true || $force == false ]] || { echo 'FORCE_RECREATE is not valid' >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$work/key"
# Each run starts with no known hosts and trusts the key it first sees at the reserved IP;
# DigitalOcean publishes no host key to check it against.
ssh_opts=(-i "$work/key" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15
  -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$work/known_hosts")
target="$DEPLOY_USER@$DEPLOY_HOST"
root=/opt/finvest
release="$root/releases/$REVISION-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"

# Installs Docker when the host lacks it. Deploy in a later session so a new docker group applies.
ssh "${ssh_opts[@]}" "$target" "bash -s -- $root" < "$(dirname "$0")/prepare-host.sh"
ssh "${ssh_opts[@]}" "$target" "umask 077; mkdir -p $root/releases && mkdir $release"
scp "${ssh_opts[@]}" -q -p -r "$bundle/." "$target:$release/"
# stdin carries only the short-lived registry token. Docker auth is removed even on failure.
script="set -euo pipefail
export DOCKER_CONFIG=\$(mktemp -d)
trap 'rm -rf -- \"\$DOCKER_CONFIG\"' EXIT
docker login ghcr.io --username '$REGISTRY_USER' --password-stdin
cd $release
bash deploy.sh $force"
printf '%s\n' "$REGISTRY_TOKEN" | ssh "${ssh_opts[@]}" "$target" "bash -c $(printf '%q' "$script")"
