#!/bin/sh
# Off camera: build the starting point of the story from nothing, then save it as "start".
#   setup/seed.sh
# A week before the story: a fresh stack, the two staff, the specimen documents, the
# 21 Oct auction rates and the 5 year bond. Then the clock moves to day 0, the day Esther signs up.
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
. "$HERE/stack/env.sh"
PV_START_OFFSET=-7 "$HERE/stack/up.sh" --fresh
cd "$REPO/backend"
(
  backend_env
  export FINVEST_DATABASE_URL=$(url_for app)
  for who in "ruth.achieng Ruth_Achieng admin" "joel.mugisha Joel_Mugisha finance"; do
    set -- $who
    link=$(LD_PRELOAD=$FAKETIME_LIB FAKETIME=$(faketime_spec) uv run --locked python -m app.modules.identity.bootstrap \
      --email "$1@finvest-demo.test" --name "$(echo "$2" | tr _ ' ')" --role "$3" 2>/dev/null | tail -1)
    echo "$1 $link" >> "$WORK/enrol-links.txt"
  done
)
cd "$HERE"
node setup/enrol-staff.mjs ruth.achieng@finvest-demo.test "$(grep ruth.achieng "$WORK/enrol-links.txt" | tail -1 | cut -d' ' -f2)"
node setup/enrol-staff.mjs joel.mugisha@finvest-demo.test "$(grep joel.mugisha "$WORK/enrol-links.txt" | tail -1 | cut -d' ' -f2)"
node setup/make-documents.mjs
node setup/seed-rates.mjs
node setup/seed-bond.mjs
"$HERE/stack/clock.sh" +7
"$HERE/stack/backup.sh" start
