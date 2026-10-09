#!/usr/bin/env bash
# OCI-N&T guarded one-shot release. Disruptive APPLY requires explicit consent.
set -Eeuo pipefail
umask 077
cd "$(dirname "$0")/.."
[[ $# -ge 1 && $# -le 2 ]] || { echo "Usage: bash deploy/release-one-shot.sh SHA [--apply]"; exit 2; }
SHA="$1"; MODE=""
if [[ $# == 2 ]]; then MODE="$2"; fi
[[ "$SHA" =~ ^[0-9a-f]{40}$ && ( -z "$MODE" || "$MODE" == "--apply" ) ]] || exit 2
OLD="ghcr.io/noahting55/oci-noah-api:sha-9d64b5da8f2c0252a36a42074ff471ef1f08f820"
TARGET="ghcr.io/noahting55/oci-noah-api:sha-$SHA"
WEB="ghcr.io/noahting55/oci-noah-web:sha-fd6a7e7b030a4bae2cad147fc4967c4fd7c87a7a"
export OCI_NT_RELEASE_BACKEND_IMAGE="$TARGET"
for svc in oci-nt-api oci-nt-monitor-agent oci-nt-docker-guard oci-nt-web; do
  [[ "$(docker inspect "$svc" --format '{{index .Config.Labels "com.docker.compose.project"}}')" == oci-nt ]] || exit 3
  [[ "$(docker inspect "$svc" --format '{{.State.Health.Status}}')" == healthy ]] || exit 3
done
for svc in oci-nt-api oci-nt-monitor-agent oci-nt-docker-guard; do
  [[ "$(docker inspect "$svc" --format '{{.Config.Image}}')" == "$OLD" ]] || exit 3
done
[[ "$(docker inspect oci-nt-web --format '{{.Config.Image}}')" == "$WEB" ]] || exit 3
docker image inspect "$TARGET" >/dev/null
docker image inspect "$OLD" >/dev/null
docker compose -f docker-compose.yml -f deploy/compose.backend-release.yml config --format json | python3 -c 'import json,os,sys; s=json.load(sys.stdin)["services"]; t=os.environ["OCI_NT_RELEASE_BACKEND_IMAGE"]; assert all(s[x]["image"]==t for x in ("api","monitor","docker-guard")); assert s["web"]["image"]!=t'
python3 deploy/backend-preflight.py --db data/oci-nt.db
echo "PLAN_OK target=$TARGET"
[[ "$MODE" == "--apply" ]] || { echo "NO_CHANGES"; exit 0; }
echo "WARNING: old API cannot prove there are zero detached OCI operations."
echo "Stop other OCI operators. Maintenance may interrupt operations."
read -r -p "Type ACCEPT OCI INTERRUPTION to authorize: " ACK
[[ "$ACK" == "ACCEPT OCI INTERRUPTION" ]] || exit 4
DIR="/root/oci-nt-backups/backend-release-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DIR"
echo "$OLD" > "$DIR/old-image"
echo "$TARGET" > "$DIR/new-image"
docker inspect oci-nt-api oci-nt-monitor-agent oci-nt-docker-guard oci-nt-web > "$DIR/inspect.json"
WEB_STOPPED=0
BACKEND_CHANGED=0
DONE=0
recover() {
  rc=$?
  trap - ERR EXIT INT TERM
  if [[ "$DONE" == 0 ]]; then
    if [[ "$BACKEND_CHANGED" == 1 ]]; then
      export OCI_NT_RELEASE_BACKEND_IMAGE="$OLD"
      docker compose -f docker-compose.yml -f deploy/compose.backend-release.yml up -d --no-deps --no-build --force-recreate docker-guard api monitor || true
    fi
    if [[ "$WEB_STOPPED" == 1 ]]; then docker start oci-nt-web >/dev/null || true; fi
    echo "RELEASE_FAILED rc=$rc backup=$DIR"
  fi
  exit "$rc"
}
trap recover ERR EXIT INT TERM
docker stop --time 30 oci-nt-web >/dev/null
WEB_STOPPED=1
sleep 120
python3 deploy/backend-preflight.py --db data/oci-nt.db > "$DIR/preflight.json"
python3 - "$DIR/preflight.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
assert r["db_task_rows_terminal"] is True
assert r["nonterminal_tasks"]=={"manual_tasks":{}, "launch_jobs":{}}
PY
python3 deploy/verified-release-backup.py --db data/oci-nt.db --output "$DIR" > "$DIR/snapshot.json"
python3 - "$DIR/snapshot.json" <<'PY'
import json,sys
assert json.load(open(sys.argv[1]))["result"]=="VERIFIED_SNAPSHOT"
PY
BACKEND_CHANGED=1
for svc in docker-guard api monitor; do
  docker compose -f docker-compose.yml -f deploy/compose.backend-release.yml up -d --no-deps --no-build --force-recreate "$svc"
done
for svc in oci-nt-docker-guard oci-nt-api oci-nt-monitor-agent; do
  OK=0
  for i in $(seq 1 45); do
    status="$(docker inspect "$svc" --format '{{.State.Health.Status}}')"
    if [[ "$status" == healthy ]]; then OK=1; break; fi
    if [[ "$status" == unhealthy ]]; then break; fi
    sleep 2
  done
  [[ "$OK" == 1 && "$(docker inspect "$svc" --format '{{.Config.Image}}')" == "$TARGET" ]] || exit 7
done
curl -fsS --max-time 5 http://127.0.0.1:9858/api/v1/health >/dev/null
docker start oci-nt-web >/dev/null
WEB_STOPPED=0
OK=0
for i in $(seq 1 30); do
  [[ "$(docker inspect oci-nt-web --format '{{.State.Health.Status}}')" == healthy ]] && { OK=1; break; }
  sleep 2
done
[[ "$OK" == 1 && "$(docker inspect oci-nt-web --format '{{.Config.Image}}')" == "$WEB" ]] || exit 7
DONE=1
trap - ERR EXIT INT TERM
echo "BACKEND_RELEASE_OK image=$TARGET backup=$DIR"
