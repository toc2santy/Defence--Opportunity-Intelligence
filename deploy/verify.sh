#!/usr/bin/env bash
# Smoke-test a deployed stack from the server (or anywhere).
#   deploy/verify.sh                 # uses DOMAIN from .env.prod
#   RESOLVE=127.0.0.1 deploy/verify.sh   # local test: skip DNS, accept the local CA
. "$(dirname "$0")/lib.sh"
need_env_file
DOMAIN="$(envval DOMAIN)"
CURL=(curl -sS -o /dev/null --max-time 15)
if [ -n "${RESOLVE:-}" ]; then
  CURL+=(-k --resolve "app.${DOMAIN}:443:${RESOLVE}" --resolve "api.${DOMAIN}:443:${RESOLVE}")
fi
fail=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then echo "  ok    $1"; else echo "  FAIL  $1 (expected $2, got $3)"; fail=1; fi
}
code() { "${CURL[@]}" -w '%{http_code}' "$@" 2>/dev/null || echo 000; }

echo "Checking https://api.${DOMAIN} and https://app.${DOMAIN}"
check "API /healthz"                 200 "$(code "https://api.${DOMAIN}/healthz")"
check "frontend served at /"         200 "$(code "https://app.${DOMAIN}/")"
check "frontend /config.js"          200 "$(code "https://app.${DOMAIN}/config.js")"
check "API docs are OFF"             404 "$(code "https://api.${DOMAIN}/docs")"
check "openapi.json is OFF"          404 "$(code "https://api.${DOMAIN}/openapi.json")"
check "protected route needs auth"   403 "$(code "https://api.${DOMAIN}/products")"
STRANGER="verify-$(date +%s)@not-on-the-allowlist.example.org"
if [ "$(envval SIGNUP_ALLOWLIST)" != "*" ]; then
  check "stranger cannot sign up"    403 "$(code -X POST -H 'Content-Type: application/json' \
      -d "{\"company_name\":\"x\",\"full_name\":\"x\",\"email\":\"${STRANGER}\",\"password\":\"a-long-enough-password\"}" \
      "https://api.${DOMAIN}/auth/signup")"
fi
HSTS="$(curl -sSkI --max-time 15 ${RESOLVE:+--resolve "api.${DOMAIN}:443:${RESOLVE}"} "https://api.${DOMAIN}/healthz" | grep -ci '^strict-transport-security' || true)"
check "HSTS header present"          1 "$HSTS"
echo
[ "$fail" = 0 ] && echo "All checks passed." || { echo "Some checks FAILED."; exit 1; }
