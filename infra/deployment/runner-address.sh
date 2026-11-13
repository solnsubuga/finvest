#!/usr/bin/env bash
# Prints this machine's public IPv4 address, for the workflows that admit their runner to SSH.
# Two unrelated services must agree, so one wrong or tampered answer cannot open port 22 to
# another address. SSH still needs the deploy key either way.
set -euo pipefail
first=$(curl -fsS --retry 3 --max-time 10 https://api.ipify.org)
second=$(curl -fsS --retry 3 --max-time 10 https://checkip.amazonaws.com)
[[ $first =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo 'No public address found' >&2; exit 1; }
[[ $first == "$second" ]] || { echo 'The address services disagree; not opening SSH' >&2; exit 1; }
printf '%s\n' "$first"
