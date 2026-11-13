#!/usr/bin/env bash
# Used by the Provision workflow.
#   check        fails with the list of missing settings for $TARGET
#   write FILE   writes the run's Terraform variables to FILE as JSON
# Comma or space separated GitHub variables become lists. Nothing here is secret.
set -euo pipefail
mode=${1:?Usage: provision-settings.sh check | write FILE}

case $mode in
  check)
    names=(DIGITALOCEAN_TOKEN SPACES_ACCESS_KEY_ID SPACES_SECRET_ACCESS_KEY CLOUDFLARE_API_TOKEN TERRAFORM_STATE_BUCKET)
    if [[ $TARGET != shared ]]; then
      names+=(GITHUB_TOKEN SSH_SOURCE_ADDRESSES DEPLOY_SSH_PUBLIC_KEY)
    fi
    if [[ $TARGET == production ]]; then
      names+=(ADMIN_ACCESS_EMAILS CLOUDFLARE_ACCOUNT_ID)
    fi
    missing=()
    for name in "${names[@]}"; do
      [[ -n ${!name:-} ]] || missing+=("$name")
    done
    if (( ${#missing[@]} )); then
      missing=("${missing[@]/#GITHUB_TOKEN/TERRAFORM_GITHUB_TOKEN}")
      echo "Missing settings for $TARGET: ${missing[*]}" >&2
      exit 1
    fi
    ;;
  write)
    out=${2:?Usage: provision-settings.sh write FILE}
    umask 077
    python3 - > "$out" <<'PY'
import json, os, re
def items(name):
    return [a for a in re.split(r"[\s,]+", os.environ.get(name, "")) if a]
json.dump({
    "deploy_ssh_public_key": os.environ.get("DEPLOY_SSH_PUBLIC_KEY", "").strip(),
    "ssh_source_addresses": items("SSH_SOURCE_ADDRESSES"),
    "admin_access_emails": items("ADMIN_ACCESS_EMAILS"),
    "alert_emails": items("ALERT_EMAILS"),
    "cloudflare_account_id": os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
    "client_app_public": os.environ.get("CLIENT_APP_PUBLIC", "") == "true",
}, __import__("sys").stdout)
PY
    ;;
  *)
    echo "Unknown mode $mode" >&2
    exit 1
    ;;
esac
