#!/bin/sh
# Move the story clock forward and restart the stack on it. The clock never goes back,
# because a day the backend has closed stays closed.
#   ./clock.sh +91     move 91 days on
set -e
STACK=$(cd "$(dirname "$0")" && pwd)
. "$STACK/env.sh"
add=${1:?usage: clock.sh +<days>}
echo $(( $(offset) ${add} )) > "$WORK/offset"
"$STACK/up.sh"
