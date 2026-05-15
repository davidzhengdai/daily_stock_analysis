#!/usr/bin/env bash
# Build the DSA Docker image and launch the News Sentinel stack.
#
# The Sentinel stack consists of two services:
#   rsshub    — diygod/rsshub (pre-built image, no build needed)
#   sentinel  — DSA image running `python -m src.services.sentinel.service --loop`
#
# Usage:
#   scripts/docker-build-launch-sentinel.sh [sentinel|rsshub|all] [compose build args...]
#
# Examples:
#   scripts/docker-build-launch-sentinel.sh              # build + start both
#   scripts/docker-build-launch-sentinel.sh all          # same as above
#   scripts/docker-build-launch-sentinel.sh sentinel     # only sentinel (assumes rsshub already running)
#   scripts/docker-build-launch-sentinel.sh rsshub       # only rsshub
#   scripts/docker-build-launch-sentinel.sh all --no-cache
#   scripts/docker-build-launch-sentinel.sh --dry-run    # run one cycle and exit (no loop)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.yml"

SERVICE="${1:-all}"

usage() {
  cat <<'EOF'
Usage: scripts/docker-build-launch-sentinel.sh [sentinel|rsshub|all|--dry-run] [compose build args...]

Build the DSA Docker image and start the News Sentinel services.

Services:
  sentinel   News Sentinel loop (uses DSA image + RSSHub)
  rsshub     RSSHub RSS synthesiser (pre-built diygod/rsshub image)
  all        Both rsshub and sentinel (default)

Special modes:
  --dry-run  Run one fetch cycle with no DB writes (good for smoke-testing)

Options forwarded to 'docker compose build':
  --no-cache  Rebuild from scratch

Examples:
  scripts/docker-build-launch-sentinel.sh
  scripts/docker-build-launch-sentinel.sh all --no-cache
  scripts/docker-build-launch-sentinel.sh sentinel
  scripts/docker-build-launch-sentinel.sh --dry-run
EOF
}

if [[ "${SERVICE}" == "-h" || "${SERVICE}" == "--help" ]]; then
  usage
  exit 0
fi

# ── detect docker compose ────────────────────────────────────────────────────

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "ERROR: Docker Compose is not available. Install Docker Compose v2 or docker-compose." >&2
  exit 1
fi

# ── dry-run mode (one-shot, no --loop) ───────────────────────────────────────

if [[ "${SERVICE}" == "--dry-run" ]]; then
  echo "=== Sentinel dry-run (one cycle, no writes) ==="
  if [[ ! -f "${ROOT_DIR}/.env" ]]; then
    echo "Warning: .env not found — runtime config may be incomplete." >&2
  fi
  cd "${ROOT_DIR}"
  echo "Building sentinel image..."
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" build sentinel
  echo ""
  echo "Running dry-run cycle..."
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" run --rm \
    -e SENTINEL_ENABLED=true \
    -e SENTINEL_RSSHUB_BASE_URL=http://rsshub:1200 \
    -e SENTINEL_REQUEST_DELAY_SECONDS=0 \
    sentinel \
    python -m src.services.sentinel.service --dry-run
  exit 0
fi

# ── normal launch mode ───────────────────────────────────────────────────────

shift || true
EXTRA_ARGS=("$@")

case "${SERVICE}" in
  sentinel)
    BUILD_SERVICES=(sentinel)
    UP_SERVICES=(sentinel)
    ;;
  rsshub)
    BUILD_SERVICES=()          # rsshub uses a pre-built image
    UP_SERVICES=(rsshub)
    ;;
  all)
    BUILD_SERVICES=(sentinel)
    UP_SERVICES=(rsshub sentinel)
    ;;
  *)
    echo "ERROR: Unknown service: ${SERVICE}" >&2
    usage >&2
    exit 2
    ;;
esac

cd "${ROOT_DIR}"

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  echo "Warning: .env not found — Compose will start but runtime config may be incomplete." >&2
fi

# Pull rsshub image when needed (no build step)
if [[ " ${UP_SERVICES[*]} " == *" rsshub "* ]]; then
  echo "Pulling latest RSSHub image..."
  docker pull diygod/rsshub:latest
fi

# Build DSA image for sentinel
if [[ ${#BUILD_SERVICES[@]} -gt 0 ]]; then
  echo "Building Docker image for: ${BUILD_SERVICES[*]}"
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" build "${EXTRA_ARGS[@]}" "${BUILD_SERVICES[@]}"
fi

echo ""
echo "Starting services: ${UP_SERVICES[*]}"
"${COMPOSE[@]}" -f "${COMPOSE_FILE}" up -d "${UP_SERVICES[@]}"

echo ""
echo "Current stack status:"
"${COMPOSE[@]}" -f "${COMPOSE_FILE}" ps

echo ""
echo "Useful commands:"
echo "  View sentinel logs : docker logs -f stock-sentinel"
echo "  View rsshub logs   : docker logs -f rsshub"
echo "  Stop sentinel      : docker stop stock-sentinel"
echo "  Stop all           : docker compose -f docker/docker-compose.yml stop rsshub sentinel"
echo "  RSSHub health      : curl http://localhost:${RSSHUB_PORT:-1200}/healthz"
