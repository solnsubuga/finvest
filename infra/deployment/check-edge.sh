#!/usr/bin/env bash
# Reads the security headers and TLS of a running environment from the outside, as a visitor's
# browser would meet them, and fails if one is missing. It only sends GET and HEAD requests and
# TLS handshakes, never a sign-in or anything that changes data.
#
#   infra/deployment/check-edge.sh [ZONE]        ZONE defaults to finvestug.com (production)
#
# Hosts behind Cloudflare Access (admin, and app until CLIENT_APP_PUBLIC) answer with Cloudflare's
# login redirect, so only their TLS and the redirect can be read from outside. Session cookies are
# set only by a finished sign-in; every cookie seen here (Cloudflare's own included) is checked
# for Secure and HttpOnly.
set -uo pipefail

zone="${1:-finvestug.com}"
failures=0
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

pass() { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }
note() { printf '  note  %s\n' "$1"; }

# header NAME: every value of that response header from the last fetch, any case.
header() {
  tr -d '\r' <"$work/headers" \
    | awk -v name="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" -F': ' \
      'tolower($1) == name { sub(/^[^:]*: /, ""); print }'
}

fetch() {
  : >"$work/headers"
  status="$(curl -sS -o /dev/null -D "$work/headers" -w '%{http_code}' --max-time 20 "$1" 2>"$work/error")" || status=000
}

expect_header() { # NAME EXTENDED-REGEX
  local value
  value="$(header "$1" | head -n 1)"
  if [ -n "$value" ] && printf '%s' "$value" | grep -Eq -- "$2"; then
    pass "$1: $value"
  else
    fail "$1 is '${value:-missing}', expected /$2/"
  fi
}

check_cookies() {
  local cookie
  while IFS= read -r cookie; do
    [ -n "$cookie" ] || continue
    local name="${cookie%%=*}"
    if printf '%s' "$cookie" | grep -iq '; *secure' && printf '%s' "$cookie" | grep -iq '; *httponly'; then
      pass "cookie $name is Secure and HttpOnly"
    else
      fail "cookie $name lacks Secure or HttpOnly"
    fi
  done < <(header Set-Cookie)
}

check_tls() { # HOST
  local host="$1" version
  for version in tls1_2 tls1_3; do
    if openssl s_client -connect "$host:443" -servername "$host" "-$version" </dev/null >"$work/tls" 2>&1 \
      && grep -q 'Verify return code: 0 (ok)' "$work/tls"; then
      pass "$version handshake with a valid certificate"
    else
      fail "$version handshake failed or the certificate did not verify"
    fi
  done
  # Security level 0 lets this client offer TLS 1.1 at all, so a refusal is the server's.
  if openssl s_client -connect "$host:443" -servername "$host" -tls1_1 -cipher 'DEFAULT@SECLEVEL=0' \
    </dev/null >/dev/null 2>&1; then
    fail "TLS 1.1 is accepted"
  else
    pass "TLS 1.1 is refused"
  fi
  fetch "http://$host/"
  if [[ "$status" =~ ^30[178]$ ]] && header Location | grep -q "^https://$host"; then
    pass "plain HTTP redirects to HTTPS ($status)"
  else
    fail "plain HTTP answered $status, expected a redirect to https://$host"
  fi
}

# Behind Access: Cloudflare redirects to its own login page.
behind_access() {
  [[ "$status" =~ ^30[1278]$ ]] && header Location | grep -q 'cloudflareaccess.com'
}

check_shared_headers() { # CSP-REGEX
  expect_header Strict-Transport-Security 'max-age=(3153[6-9][0-9]{3}|31[6-9][0-9]{5}|3[2-9][0-9]{6}|[4-9][0-9]{7}|[0-9]{9,}); *includeSubDomains'
  expect_header X-Content-Type-Options '^nosniff$'
  expect_header X-Frame-Options '^DENY$'
  expect_header Referrer-Policy '^strict-origin-when-cross-origin$'
  expect_header Cross-Origin-Opener-Policy '^same-origin$'
  expect_header Content-Security-Policy "$1"
  check_cookies
}

csp_api="^default-src 'none'; frame-ancestors 'none'$"
# A page policy: frame-ancestors 'none', no plugins, and no inline or eval script.
csp_page="frame-ancestors 'none'"

echo "api.$zone"
check_tls "api.$zone"
fetch "https://api.$zone/health/live"
if [ "$status" = 200 ]; then pass "GET /health/live answered 200"; else fail "GET /health/live answered $status"; fi
check_shared_headers "$csp_api"
expect_header X-Robots-Tag 'noindex'
expect_header Permissions-Policy 'camera=\(\)'

for host in "app.$zone" "admin.$zone"; do
  echo "$host"
  check_tls "$host"
  fetch "https://$host/"
  if behind_access; then
    note "behind Cloudflare Access ($status to the login page): origin headers are read only after sign-in"
    check_cookies
    continue
  fi
  if [ "$status" = 200 ]; then pass "GET / answered 200"; else fail "GET / answered $status"; fi
  check_shared_headers "$csp_page"
  expect_header X-Robots-Tag 'noindex'
  policy="$(header Content-Security-Policy | head -n 1)"
  if printf '%s' "$policy" | grep -Eq "script-src[^;]*'(unsafe-inline|unsafe-eval)'"; then
    fail "the policy lets inline or eval script run"
  else
    pass "no inline or eval script allowed"
  fi
  if printf '%s' "$policy" | grep -q "object-src 'none'"; then pass "object-src 'none'"; else fail "object-src is not 'none'"; fi
  if [ "$host" = "app.$zone" ]; then
    expect_header Permissions-Policy 'camera=\(self\)'
  else
    expect_header Permissions-Policy 'camera=\(\)'
  fi
done

echo "$zone"
check_tls "$zone"
fetch "https://$zone/"
case "$status" in
  200) pass "GET / answered 200 (published)"; check_shared_headers "$csp_page" ;;
  503) pass "GET / answered 503 (not published yet)"; check_shared_headers "$csp_api" ;;
  *) fail "GET / answered $status" ;;
esac

echo "www.$zone"
fetch "https://www.$zone/"
if [ "$status" = 308 ] && header Location | grep -q "^https://$zone/"; then
  pass "redirects to https://$zone/"
else
  fail "answered $status, expected 308 to https://$zone/"
fi
expect_header Strict-Transport-Security 'includeSubDomains'

echo
if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed."
  exit 1
fi
echo "Every check passed."
