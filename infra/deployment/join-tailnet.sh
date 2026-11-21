#!/usr/bin/env bash
# Puts this server on Finvest's tailnet, once, so the back office can be served only there
# (owner, 2026-11-18; infra/README.md). Run by the Provision host step as the deploy user:
#
#   printf '%s\n' "$TAILSCALE_AUTH_KEY" | bash join-tailnet.sh finvest-production
#
# The auth key arrives on stdin, never as an argument, and is kept only in a file only root can
# read for as long as `tailscale up` needs it. The key should be tagged tag:finvest-server, so the
# server's own key never expires and the tailnet policy decides who may reach it. Prints the
# server's Tailscale IPv4 address as the last line.
set -euo pipefail
hostname=${1:?Usage: join-tailnet.sh HOSTNAME < auth-key}
[[ $hostname =~ ^[a-z0-9-]{1,63}$ ]] || { echo "Hostname is not valid" >&2; exit 1; }

if ! command -v tailscale >/dev/null; then
  # Tailscale's own package repository, signed with its key (https://pkgs.tailscale.com).
  # shellcheck disable=SC1091
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/$codename.noarmor.gpg" |
    sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/$codename.tailscale-keyring.list" |
    sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tailscale >/dev/null
fi
sudo systemctl enable --now tailscaled >/dev/null

# Already on the tailnet: nothing to change, and the auth key is not used again.
if ! address=$(sudo tailscale ip -4 2>/dev/null) || [[ -z $address ]]; then
  IFS= read -r key
  [[ -n $key ]] || { echo "No auth key on stdin" >&2; exit 1; }
  keyfile=$(sudo mktemp)
  trap 'sudo rm -f "$keyfile"' EXIT
  printf '%s' "$key" | sudo tee "$keyfile" >/dev/null
  # No Tailscale SSH and no tailnet DNS on the server: SSH stays as Provision set it up, and the
  # containers keep resolving through the host's own resolver.
  sudo tailscale up --auth-key="file:$keyfile" --hostname="$hostname" \
    --advertise-tags=tag:finvest-server --ssh=false --accept-dns=false --accept-routes=false
  address=$(sudo tailscale ip -4)
fi
printf '%s\n' "$address"
