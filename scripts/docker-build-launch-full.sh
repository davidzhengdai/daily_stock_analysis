#!/usr/bin/env bash
# Build the DSA image and start the full stack:
#   server   — FastAPI stock analysis server
#   sentinel — News Sentinel scraping loop
#   rsshub   — RSSHub RSS synthesiser (required by sentinel)
#
# The three containers share the same /app/data volume so the sentinel DB is
# visible to the main server without any extra configuration.
#
# Usage:
#   scripts/docker-build-launch-full.sh [--no-cache] [--no-sentinel]
#
# Options:
#   --no-cache      Rebuild the DSA Docker image from scratch
#   --no-sentinel   Start only the stock server (skip sentinel + rsshub)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.yml"

NO_CACHE=false
WITH_SENTINEL=true

for arg in "$@"; do
  case "${arg}" in
    --no-cache)    NO_CACHE=true ;;
    --no-sentinel) WITH_SENTINEL=false ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/docker-build-launch-full.sh [--no-cache] [--no-sentinel]

Start the stock server and (optionally) the News Sentinel together.

Options:
  --no-cache      Rebuild the DSA Docker image from scratch
  --no-sentinel   Start only the stock server (skip sentinel + rsshub)

Examples:
  scripts/docker-build-launch-full.sh
  scripts/docker-build-launch-full.sh --no-cache
  scripts/docker-build-launch-full.sh --no-sentinel
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: ${arg}" >&2
      echo "Run with --help for usage." >&2
      exit 1
      ;;
  esac
done

# ── detect docker compose ─────────────────────────────────────────────────────

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "ERROR: Docker Compose is not available. Install Docker Compose v2 or docker-compose." >&2
  exit 1
fi

cd "${ROOT_DIR}"

if [[ ! -f ".env" ]]; then
  echo "Warning: .env not found — runtime config may be incomplete." >&2
fi

# ── build DSA image ───────────────────────────────────────────────────────────

BUILD_ARGS=()
[[ "${NO_CACHE}" == "true" ]] && BUILD_ARGS+=(--no-cache)

if [[ "${WITH_SENTINEL}" == "true" ]]; then
  echo "=== Building DSA image (shared by server + sentinel) ==="
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" build "${BUILD_ARGS[@]}" server sentinel
else
  echo "=== Building DSA image (server only) ==="
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" build "${BUILD_ARGS[@]}" server
fi

# ── pull rsshub image ─────────────────────────────────────────────────────────

if [[ "${WITH_SENTINEL}" == "true" ]]; then
  echo ""
  echo "=== Pulling RSSHub image ==="
  docker pull diygod/rsshub:latest
fi

# ── start services ────────────────────────────────────────────────────────────

echo ""
if [[ "${WITH_SENTINEL}" == "true" ]]; then
  echo "=== Starting: server + sentinel + rsshub ==="
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" up -d server rsshub sentinel
else
  echo "=== Starting: server only ==="
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" up -d server
fi

# ── status + hints ────────────────────────────────────────────────────────────

echo ""
echo "=== Stack status ==="
"${COMPOSE[@]}" -f "${COMPOSE_FILE}" ps

API_PORT="${API_PORT:-8000}"
RSSHUB_PORT="${RSSHUB_PORT:-1200}"
SENTINEL_SERVER_PORT="${SENTINEL_SERVER_PORT:-9100}"

echo ""
echo "=== Useful commands ==="
echo "  Server logs    : docker logs -f stock-server"
if [[ "${WITH_SENTINEL}" == "true" ]]; then
  echo "  Sentinel logs  : docker logs -f stock-sentinel"
  echo "  RSSHub logs    : docker logs -f rsshub"
fi
echo ""
echo "  Server API     : http://localhost:${API_PORT}/docs"
if [[ "${WITH_SENTINEL}" == "true" ]]; then
  echo "  Sentinel health: curl http://localhost:${RSSHUB_PORT}/healthz   (rsshub)"
  echo "  Sentinel status: curl http://localhost:${API_PORT}/api/v1/sentinel/status"
fi
echo ""
echo "  Stop all       : docker compose -f docker/docker-compose.yml down"
if [[ "${WITH_SENTINEL}" == "true" ]]; then
  echo "  Stop sentinel  : docker stop stock-sentinel rsshub"
fi
