#!/usr/bin/env bash
# Prints a hash of the planned resource changes in a saved plan file.
# Used to check that the applied plan is the plan the approver reviewed.
# Only the hash is printed. The JSON can hold secret values and is never written out.
set -euo pipefail
terraform show -json "$1" | jq -S '[.resource_changes[]? | {address, actions: .change.actions, after: .change.after}]' | sha256sum | cut -d' ' -f1
