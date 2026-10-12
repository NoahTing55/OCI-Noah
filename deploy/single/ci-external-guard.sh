#!/usr/bin/env bash
# Disposable GitHub-hosted runner only. No production paths, accounts or OCI calls.
set -Eeuo pipefail
[[ "${GITHUB_ACTIONS:-}" == "true" && -n "${GITHUB_RUN_ID:-}" ]] || { echo "GITHUB_CI_ONLY"; exit 3; }
[[ "${GITHUB_WORKSPACE:-}" == "$(pwd)" ]] || { echo "WRONG_WORKSPACE"; exit 3; }
tmp="$(mktemp -d)"
project="oci-nt-guard-ci-${GITHUB_RUN_ID}"
compose="$tmp/override.yml"
export DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
[[ "$DOCKER_GID" =~ ^[0-9]+$ && "$DOCKER_GID" -gt 0 ]] || { echo "BAD_DOCKER_GID"; exit 3; }
cleanup() {
  docker compose -p "$project" -f deploy/compose.external-guard-lab.yml -f "$compose" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT
mkdir -p "$tmp/data" "$tmp/logs"
chmod 777 "$tmp/data" "$tmp/logs"
key="$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
# CI-only override: disposable DB/logs and credentials; no real production bind.
cat > "$compose" <<YAML
services:
  all-in-one:
    volumes:
      - $tmp/data:/app/data
      - $tmp/logs:/app/logs
    environment:
      SECRET_KEY: ci-external-guard-only
      CREDENTIAL_ENCRYPTION_KEY: "$key"
      ADMIN_USERNAME: ci-admin
      ADMIN_PASSWORD: ci-password
YAML
# The base compose path also references ../data and ../logs. Verify the rendered
# effective mounts replace those destinations with disposable paths, never prod.
docker compose -p "$project" -f deploy/compose.external-guard-lab.yml -f "$compose" config --format json |
 python3 -c '
import json,sys
s=json.load(sys.stdin)["services"]
w=s["all-in-one"]
g=s["docker-guard"]
assert not any("/var/run/docker.sock" in (str(m.get("source"))+" "+str(m.get("target"))) for m in w["volumes"])
assert not w.get("group_add")
for m in w["volumes"]:
 if m["target"] in ("/app/data","/app/logs"):
  assert m["source"].startswith("/tmp/"),m
assert g["network_mode"]=="none"
'
docker compose -p "$project" -f deploy/compose.external-guard-lab.yml -f "$compose" build
docker compose -p "$project" -f deploy/compose.external-guard-lab.yml -f "$compose" up -d
guard="$(docker compose -p "$project" -f deploy/compose.external-guard-lab.yml -f "$compose" ps -q docker-guard)"
workload="$(docker compose -p "$project" -f deploy/compose.external-guard-lab.yml -f "$compose" ps -q all-in-one)"
[[ -n "$guard" && -n "$workload" ]] || exit 1
healthy=0
for i in $(seq 1 60); do
 if [[ "$(docker inspect "$guard" -f '{{.State.Running}}')" != "true" || "$(docker inspect "$workload" -f '{{.State.Running}}')" != "true" ]]; then
  docker logs "$guard" || true; docker logs "$workload" || true; exit 1
 fi
 if docker exec "$workload" python /app/health.py >/dev/null 2>&1; then healthy=1;break;fi
 sleep 2
done
[[ "$healthy" == 1 ]] || { docker logs "$guard";docker logs "$workload";exit 1; }
echo "EXTERNAL_GUARD_STACK_HEALTHY"
docker exec "$workload" python - <<'PY'
import os,stat
from app import monitor_agent
assert not os.path.exists("/var/run/docker.sock")
assert monitor_agent.DOCKER_GUARD_UNIX_SOCKET
assert monitor_agent.docker_get("/version").get("Version")
print("WORKLOAD_NO_RAW_SOCKET_AND_MONITOR_OK")
PY
docker exec "$workload" python -c 'import http.client;from app.monitor_agent import UnixHTTPConnection,DOCKER_GUARD_UNIX_SOCKET as p;c=UnixHTTPConnection(p);c.request("POST","/version");r=c.getresponse();assert r.status==405,r.status;r.read();c.close()'
docker exec "$workload" python -c 'from app.monitor_agent import UnixHTTPConnection,DOCKER_GUARD_UNIX_SOCKET as p;c=UnixHTTPConnection(p);c.request("GET","/containers/create");r=c.getresponse();assert r.status==403,r.status;r.read();c.close()'
echo "GUARD_MUTATIONS_DENIED"
docker stop "$guard" >/dev/null
docker exec "$workload" python -c 'from app.monitor_agent import docker_get;import sys
try: docker_get("/version")
except Exception: print("GUARD_UNAVAILABLE_FAILS_CLOSED")
else: sys.exit("FAIL_OPEN")'
docker start "$guard" >/dev/null
recovered=0
for i in $(seq 1 30); do
 if docker exec "$workload" python -c 'from app.monitor_agent import docker_get;assert docker_get("/version").get("Version")' >/dev/null 2>&1;then recovered=1;break;fi
 sleep 2
done
[[ "$recovered" == 1 ]] || exit 1
echo "GUARD_RESTART_RECOVERED"
docker stop --time 35 "$workload" >/dev/null
[[ "$(docker inspect "$workload" -f '{{.State.ExitCode}}')" == 0 ]] || exit 1
echo "WORKLOAD_GRACEFUL_STOP_OK"
echo "DISPOSABLE_EXTERNAL_GUARD_INTEGRATION_OK"
