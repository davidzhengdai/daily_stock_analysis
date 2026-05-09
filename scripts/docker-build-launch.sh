#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.yml"
SERVICE="${1:-server}"

usage() {
  cat <<'EOF'
Usage: scripts/docker-build-launch.sh [server|analyzer|all] [compose build args...]

Build the Docker image first, then launch the selected Docker Compose service.

Examples:
  scripts/docker-build-launch.sh
  scripts/docker-build-launch.sh server
  scripts/docker-build-launch.sh analyzer
  scripts/docker-build-launch.sh all
  scripts/docker-build-launch.sh server --no-cache
EOF
}

if [[ "${SERVICE}" == "-h" || "${SERVICE}" == "--help" ]]; then
  usage
  exit 0
fi

shift || true
EXTRA_ARGS=("$@")

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "Docker Compose is not available. Install Docker Compose v2 or docker-compose." >&2
  exit 1
fi

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  echo "Warning: .env not found. Compose will still start, but runtime config may be incomplete." >&2
fi

case "${SERVICE}" in
  server)
    SERVICES=(server)
    ;;
  analyzer)
    SERVICES=(analyzer)
    ;;
  all)
    SERVICES=(analyzer server)
    ;;
  *)
    echo "Unknown service: ${SERVICE}" >&2
    usage >&2
    exit 2
    ;;
esac

cd "${ROOT_DIR}"

echo "Building Docker image for: ${SERVICES[*]}"
"${COMPOSE[@]}" -f "${COMPOSE_FILE}" build "${EXTRA_ARGS[@]}" "${SERVICES[@]}"

echo "Launching Docker service(s): ${SERVICES[*]}"
"${COMPOSE[@]}" -f "${COMPOSE_FILE}" up -d "${SERVICES[@]}"

echo "Current Docker Compose status:"
"${COMPOSE[@]}" -f "${COMPOSE_FILE}" ps
