#!/usr/bin/env bash
# Ported from Fidiba. Make a Debian or Ubuntu host ready for deployments: Docker Engine with Compose 2.30 or later from
# Docker's signed apt repository, Docker access for the deployment account and its root directory.
# The Provision run (and later each rollout) runs it over SSH and changes nothing on a host that is already ready.
set -euo pipefail

root=${1:?Usage: prepare-host.sh DEPLOY_ROOT}
[[ $root =~ ^/[a-zA-Z0-9_/-]+$ ]] || { echo 'Invalid deployment root' >&2; exit 1; }
minimum_compose=2.30.0
# Docker's release signing key: https://docs.docker.com/engine/install/debian/
docker_key=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
account=$(id -un)

can_elevate() {
  [[ $(id -u) == 0 ]] || sudo -n true 2>/dev/null
}

privileged() {
  if [[ $(id -u) == 0 ]]; then
    "$@"
  elif sudo -n true 2>/dev/null; then
    sudo -n "$@"
  else
    echo "Preparing this host needs root or passwordless sudo for $account: $*" >&2
    exit 1
  fi
}

compose_ready() {
  local version
  # The account may already use Docker through its group without any sudo rights.
  version=$(docker compose version --short 2>/dev/null) ||
    { can_elevate && version=$(privileged docker compose version --short 2>/dev/null); } ||
    return 1
  version=${version#v}
  [[ $(printf '%s\n%s\n' "$minimum_compose" "$version" | sort -V | head -n 1) == "$minimum_compose" ]]
}

install_docker() {
  # shellcheck source=/dev/null
  . /etc/os-release
  local distribution=${ID:-}
  if [[ $distribution != debian && $distribution != ubuntu ]]; then
    echo "Install Docker Engine and Compose $minimum_compose+ manually on ${PRETTY_NAME:-this host}" >&2
    exit 1
  fi
  if dpkg -s docker.io >/dev/null 2>&1 || dpkg -s podman-docker >/dev/null 2>&1; then
    echo 'Remove the distribution Docker packages before installing Docker Engine' >&2
    exit 1
  fi
  local apt=(env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 -y -q)
  privileged "${apt[@]}" update
  privileged "${apt[@]}" install ca-certificates curl gnupg util-linux
  local key fingerprints
  key=$(mktemp)
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    "https://download.docker.com/linux/$distribution/gpg" --output "$key"
  fingerprints=$(gpg --show-keys --with-colons "$key" | awk -F: '$1 == "fpr" { print $10 }')
  if ! grep -qx "$docker_key" <<< "$fingerprints"; then
    rm -f -- "$key"
    echo 'Unexpected Docker signing key' >&2
    exit 1
  fi
  privileged install -d -m 0755 /etc/apt/keyrings
  privileged install -m 0644 "$key" /etc/apt/keyrings/docker.asc
  rm -f -- "$key"
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$distribution" "${VERSION_CODENAME:?}" |
    privileged tee /etc/apt/sources.list.d/docker.list >/dev/null
  privileged "${apt[@]}" update
  privileged "${apt[@]}" install docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin
  privileged systemctl enable --now docker
}

if ! compose_ready; then
  if command -v docker >/dev/null 2>&1 && ! dpkg -s docker-ce >/dev/null 2>&1; then
    echo "Upgrade this host's Docker to Docker Engine with Compose $minimum_compose+" >&2
    exit 1
  fi
  echo 'Installing Docker Engine and Compose from download.docker.com'
  install_docker
  compose_ready || { echo "Docker Compose $minimum_compose+ is still unavailable" >&2; exit 1; }
fi
for tool in flock curl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Install $tool on this host" >&2; exit 1; }
done

# The next SSH session picks up a new docker group membership; this one keeps its old groups.
if [[ $(id -u) != 0 ]] && ! id -nG "$account" | tr ' ' '\n' | grep -qx docker; then
  privileged usermod -aG docker "$account"
  echo "Added $account to the docker group"
fi
if [[ ! -d $root || ! -w $root ]]; then
  privileged install -d -m 0750 -o "$account" -g "$(id -gn)" "$root"
fi
echo 'Host ready for deployment.'
