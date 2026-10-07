#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$ROOT/docker-compose.ui21-preview.yml"
CONTAINER="oci-nt-web-ui21-preview"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

case "${1:-start}" in
  start)
    log "检查生产 API"
    curl -fsS http://127.0.0.1:9858/api/v1/health
    echo

    log "构建并启动 UI 2.1 Preview"
    cd "$ROOT"
    docker compose -f "$COMPOSE" up -d --build

    log "等待 Preview 健康"
    for _ in $(seq 1 30); do
      state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true)"
      if [[ "$state" == "healthy" ]]; then
        echo "UI21_PREVIEW_OK"
        echo "LOCAL_URL=http://127.0.0.1:9869"
        exit 0
      fi
      sleep 1
    done

    docker logs --tail=100 "$CONTAINER" || true
    echo "UI21_PREVIEW_HEALTH_TIMEOUT" >&2
    exit 1
    ;;
  stop)
    log "停止 UI 2.1 Preview"
    cd "$ROOT"
    docker compose -f "$COMPOSE" down
    ;;
  status)
    cd "$ROOT"
    docker compose -f "$COMPOSE" ps
    ;;
  logs)
    docker logs --tail=200 -f "$CONTAINER"
    ;;
  *)
    echo "Usage: $0 {start|stop|status|logs}" >&2
    exit 2
    ;;
esac
