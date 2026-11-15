#!/bin/sh
# Off camera: add a member of staff with the command-line bootstrap and finish their set-up.
#   setup/add-staff.sh <email> "<name>" <role>
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
. "$HERE/stack/env.sh"
cd "$REPO/backend"
link=$(
  backend_env
  export FINVEST_DATABASE_URL=$(url_for app)
  LD_PRELOAD=$FAKETIME_LIB FAKETIME=$(faketime_spec) uv run --locked python -m app.modules.identity.bootstrap \
    --email "$1" --name "$2" --role "$3" 2>/dev/null | tail -1
)
cd "$HERE"
node setup/enrol-staff.mjs "$1" "$link"
