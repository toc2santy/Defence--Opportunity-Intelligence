#!/usr/bin/env bash
# Build and (re)start the production stack. Run from anywhere on the
# server as the deploy user; safe to re-run for every update:
#   cd /opt/doi && git pull && deploy/deploy.sh
. "$(dirname "$0")/lib.sh"
need_env_file

# 1. Fail early with a precise message if any required variable is empty
#    (docker-compose.prod.yml marks them all required).
"${COMPOSE[@]}" config -q

# 2. Proxy mode: Cloudflare in front (recommended) or direct.
mkdir -p deploy/generated
if [ "$(envval USE_CLOUDFLARE)" = "yes" ]; then
  echo "Proxy mode: Cloudflare — fetching current Cloudflare IP ranges"
  RANGES="$( (curl -fsS https://www.cloudflare.com/ips-v4; echo; curl -fsS https://www.cloudflare.com/ips-v6) | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//')"
  [ -n "$RANGES" ] || { echo "Could not fetch Cloudflare ranges — refusing to fall back to an open origin."; exit 1; }
  cat > deploy/generated/proxy-mode.caddy <<CADDY
(origin_guard) {
	# Let's Encrypt's HTTP-01 challenge (issue AND renewal) comes from
	# Let's Encrypt, not Cloudflare: never block that path.
	@not_cloudflare {
		not remote_ip ${RANGES}
		not path /.well-known/acme-challenge/*
	}
	abort @not_cloudflare
}
(client_ip) {
	header_up X-Forwarded-For {http.request.header.CF-Connecting-IP}
}
CADDY
else
  echo "Proxy mode: direct (no Cloudflare). Rate limits key on the TCP peer address."
  cat > deploy/generated/proxy-mode.caddy <<'CADDY'
(origin_guard) {
	# direct mode: nothing to guard
}
(client_ip) {
	header_up X-Forwarded-For {remote_host}
}
CADDY
fi

# 3. Database first, so migrations run before the new API code serves traffic.
"${COMPOSE[@]}" build
"${COMPOSE[@]}" up -d db
echo -n "Waiting for Postgres (TCP — the first-boot init server only listens on the socket)"
for i in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T db pg_isready -h 127.0.0.1 -U postgres -d doi >/dev/null 2>&1; then echo " ready"; break; fi
  echo -n "."; sleep 2
  [ "$i" = 60 ] && { echo " timed out"; exit 1; }
done
deploy/migrate.sh

# 4. Everything else.
"${COMPOSE[@]}" up -d
# `up -d` does NOT restart caddy when only the generated proxy-mode file
# changed (the compose definition is identical), so a switch to/from
# Cloudflare mode — or a refreshed IP-range list — would silently not
# take effect, leaving the origin open. Caught by the local smoke test.
# Reload is graceful (no dropped connections).
"${COMPOSE[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
  || { echo "caddy reload failed — restarting caddy"; "${COMPOSE[@]}" restart caddy; }
echo
"${COMPOSE[@]}" ps
echo
echo "Deployed. Run deploy/verify.sh to smoke-test it."
