# Shared helpers, sourced by the other deploy scripts. Not run directly.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml)

# Read one KEY=value from the env file without sourcing it (values may
# contain characters the shell would interpret).
envval() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }

need_env_file() {
  [ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE — run deploy/gen-secrets.sh first."; exit 1; }
}
