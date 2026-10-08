#!/usr/bin/env bash
# OCI-N&T: safe, explicitly approved web-only GHCR release.
set -Eeuo pipefail

usage() {
  echo "Usage: bash deploy/release-web.sh <40-character Git commit SHA> [--apply]"
  echo "Without --apply this runs a read-only preflight; --apply pulls/recreates only web."
}
if [[ ${1:-} == "--help" || $# -lt 1 || $# -gt 2 ]]; then usage; exit 2; fi
SHA="$1"
MODE="${2:-}"
if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ || ( -n "$MODE" && "$MODE" != "--apply" ) ]]; then usage; exit 2; fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env && -f docker-compose.yml ]] || { echo "Missing production compose or .env"; exit 1; }

export OCI_NT_RELEASE_WEB_IMAGE="ghcr.io/noahting55/oci-noah-web:sha-${SHA}"
COMPOSE=(docker compose -f docker-compose.yml -f deploy/compose.web-release.yml)
actual="$("${COMPOSE[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["web"]["image"])')"
[[ "$actual" == "$OCI_NT_RELEASE_WEB_IMAGE" ]] || { echo "Web image override mismatch"; exit 1; }
old="$(docker inspect oci-nt-web --format '{{.Config.Image}}')"
health="$(docker inspect oci-nt-web --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}')"
api_health="$(docker inspect oci-nt-api --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}')"
printf 'Current web: %s\nTarget web:  %s\nWeb/API health: %s/%s\n' "$old" "$actual" "$health" "$api_health"
[[ "$api_health" == healthy ]] || { echo "API not healthy; refusing update"; exit 1; }
if [[ "$MODE" != "--apply" ]]; then
  echo "PREFLIGHT_OK: no changes made. Re-run with --apply to approve deployment."
  exit 0
fi

[[ "$health" == healthy ]] || { echo "Current Web not healthy; refusing update"; exit 1; }
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/root/oci-nt-backups/web-release-${stamp}"
umask 077
mkdir -p "$backup"
printf '%s\n' "$old" > "$backup/previous-image.txt"
printf '%s\n' "$actual" > "$backup/target-image.txt"
docker inspect oci-nt-web > "$backup/web-inspect.json"
# Never write `docker compose config` or .env into logs; these may contain credentials.
echo "Backup metadata saved: $backup"

docker pull "$OCI_NT_RELEASE_WEB_IMAGE"
docker run --rm --network none --entrypoint sh "$OCI_NT_RELEASE_WEB_IMAGE" -c '
  test -s /usr/share/nginx/html/index.html
  test -s /usr/share/nginx/html/ui-theme.css
  test -s /usr/share/nginx/html/app.js
'
echo "Image assets verified. Recreating only the Web container..."
updated=0
rollback() {
  if [[ "$updated" -eq 1 ]]; then
    echo "Web verification failed; attempting rollback to $old"
    export OCI_NT_RELEASE_WEB_IMAGE="$old"
    "${COMPOSE[@]}" up -d --no-deps --no-build --force-recreate web || true
  fi
}
trap rollback ERR
updated=1
"${COMPOSE[@]}" up -d --no-deps --no-build --force-recreate web

ok=0
for i in $(seq 1 30); do
  status="$(docker inspect oci-nt-web --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}')"
  if [[ "$status" == healthy ]]; then ok=1; break; fi
  if [[ "$status" == unhealthy ]]; then break; fi
  sleep 2
done
[[ "$ok" -eq 1 ]]
[[ "$(docker inspect oci-nt-web --format '{{.Config.Image}}')" == "$OCI_NT_RELEASE_WEB_IMAGE" ]]
curl -fsS --max-time 5 http://127.0.0.1:9859/ >/dev/null
curl -fsS --max-time 5 http://127.0.0.1:9859/ui-theme.css >/dev/null
[[ "$(docker inspect oci-nt-api --format '{{.State.Health.Status}}')" == healthy ]]
trap - ERR
updated=0
printf '%s\n' "$SHA" > "$backup/deployed-commit.txt"
echo "WEB_RELEASE_OK: $OCI_NT_RELEASE_WEB_IMAGE"
echo "Rollback image: $old"
echo "Rollback metadata: $backup"
