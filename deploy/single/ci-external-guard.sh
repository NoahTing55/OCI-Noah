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
      ACCESS_TOKEN_MINUTES: "60"
      BULK_CHECK_INTERVAL_SECONDS: "120"
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
if ! docker compose -p "$project" -f deploy/compose.external-guard-lab.yml -f "$compose" up -d; then
  docker compose -p "$project" -f deploy/compose.external-guard-lab.yml -f "$compose" ps -a || true
  docker compose -p "$project" -f deploy/compose.external-guard-lab.yml -f "$compose" logs --no-color --tail=100 || true
  exit 1
fi
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
# Runtime permissions must match the intended separation, not just Compose text.
docker exec -i --user 10001:10001 "$workload" python - <<'PY'
from pathlib import Path
import os
import stat

socket = Path("/run/oci-guard/guard.sock")
assert socket.is_socket(), "filtered guard socket missing"
mode = stat.S_IMODE(socket.stat().st_mode)
assert mode == 0o660, oct(mode)
assert not Path("/var/run/docker.sock").exists(), "raw Docker socket leaked"
required = {
    b"app.monitor_agent": 10001,
    b"app.main:app": 10001,
    b"nginx: master process": 10001,
}
found = {}
for path in Path("/proc").iterdir():
    if not path.name.isdigit():
        continue
    try:
        cmd = (path / "cmdline").read_bytes()
        status = (path / "status").read_text()
    except (OSError, PermissionError):
        continue
    for name, expected_uid in required.items():
        if name not in cmd:
            continue
        fields = {line.split(":", 1)[0]: line.split(":", 1)[1].split()
                  for line in status.splitlines() if ":" in line}
        uid = int(fields["Uid"][0])
        gids = [int(gid) for gid in fields.get("Groups", [])]
        assert uid == expected_uid, (name, uid)
        assert int(fields["CapEff"][0], 16) == 0, (name, "nonzero effective capabilities")
        assert int(fields["NoNewPrivs"][0]) == 1, (name, "no-new-privileges missing")
        found[name] = (uid, gids)
assert set(found) == set(required), (found, required)
print("EXTERNAL_GUARD_CHILD_IDENTITY_AND_SOCKET_MODE_OK")
PY
docker exec -i "$guard" python - <<'PY'
from pathlib import Path
p = Path("/proc/1/status")
fields = {line.split(":", 1)[0]: line.split(":", 1)[1].split()
          for line in p.read_text().splitlines() if ":" in line}
assert int(fields["Uid"][0]) == 10001
assert int(fields["CapEff"][0], 16) == 0
assert int(fields["NoNewPrivs"][0]) == 1
assert Path("/var/run/docker.sock").is_socket()
print("EXTERNAL_GUARD_PROCESS_BOUNDARY_OK")
PY
docker exec -i --user 10001:10001 "$workload" python - <<'PY'
import os,stat
from app import monitor_agent
assert not os.path.exists("/var/run/docker.sock")
assert monitor_agent.DOCKER_GUARD_UNIX_SOCKET
assert monitor_agent.docker_get("/version").get("Version")
print("WORKLOAD_NO_RAW_SOCKET_AND_MONITOR_OK")
PY
docker exec --user 10001:10001 "$workload" python -c 'import http.client;from app.monitor_agent import UnixHTTPConnection,DOCKER_GUARD_UNIX_SOCKET as p;c=UnixHTTPConnection(p);c.request("POST","/version");r=c.getresponse();assert r.status==405,r.status;r.read();c.close()'
docker exec --user 10001:10001 "$workload" python -c 'from app.monitor_agent import UnixHTTPConnection,DOCKER_GUARD_UNIX_SOCKET as p;c=UnixHTTPConnection(p);c.request("GET","/containers/create");r=c.getresponse();assert r.status==403,r.status;r.read();c.close()'
echo "GUARD_MUTATIONS_DENIED"
# Monitor health must explicitly report backend unavailability, not a false 200.
docker exec --user 10001:10001 "$workload" python -c 'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:9860/health",timeout=4).status == 200'
docker stop "$guard" >/dev/null
docker exec --user 10001:10001 "$workload" python -c 'from app.monitor_agent import docker_get;import sys
try: docker_get("/version")
except Exception: print("GUARD_UNAVAILABLE_FAILS_CLOSED")
else: sys.exit("FAIL_OPEN")'
docker exec --user 10001:10001 "$workload" python -c 'import urllib.request,urllib.error; u="http://127.0.0.1:9860/health"
try: urllib.request.urlopen(u,timeout=4)
except urllib.error.HTTPError as e: assert e.code == 503,e.code
else: raise SystemExit("MONITOR_FALSE_HEALTHY")'
echo "MONITOR_UNAVAILABLE_REPORTS_503"
docker start "$guard" >/dev/null
recovered=0
for i in $(seq 1 30); do
 if docker exec --user 10001:10001 "$workload" python -c 'from app.monitor_agent import docker_get;assert docker_get("/version").get("Version")' >/dev/null 2>&1;then recovered=1;break;fi
 sleep 2
done
[[ "$recovered" == 1 ]] || exit 1
docker exec --user 10001:10001 "$workload" python -c 'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:9860/health",timeout=4).status == 200'
echo "GUARD_RESTART_RECOVERED"
# CI-only: verify Docker automatic crash recovery for this disposable Guard.
# Restore the original restart policy before the temporary stack is removed.
docker update --restart on-failure:3 "$guard" >/dev/null
sleep 11
before="$(docker inspect "$guard" --format '{{.RestartCount}}')"
before_started="$(docker inspect "$guard" --format '{{.State.StartedAt}}')"
before_pid="$(docker inspect "$guard" --format '{{.State.Pid}}')"
# A Docker exec process has its own PID namespace context and may not be
# permitted to signal PID 1. Detect injection failure instead of hiding it.
# Resolve the exact Docker-managed container init PID in this disposable CI run.
# Sending SIGKILL from an exec process to PID 1 can be ignored by the runtime.
guard_host_pid="$(docker inspect "$guard" --format '{{.State.Pid}}')"
[[ "$guard_host_pid" =~ ^[0-9]+$ && "$guard_host_pid" -gt 1 ]] || { echo "INVALID_CI_GUARD_PID"; exit 1; }
[[ "$guard_host_pid" == "$before_pid" ]] || { echo "GUARD_PID_CHANGED_BEFORE_TEST"; exit 1; }
sudo kill -KILL "$guard_host_pid"
echo "GUARD_INIT_CRASH_SIGNAL_SENT"
auto=0
for i in $(seq 1 35); do
  after="$(docker inspect "$guard" --format '{{.RestartCount}}')"
  after_started="$(docker inspect "$guard" --format '{{.State.StartedAt}}')"
  after_pid="$(docker inspect "$guard" --format '{{.State.Pid}}')"
  # A new PID and StartedAt with no manual docker start prove engine recovery.
  if [[ "$after" -gt "$before" || ( "$after_started" != "$before_started" && "$after_pid" != "$before_pid" ) ]] &&
     docker exec --user 10001:10001 "$workload" python -c 'from app.monitor_agent import docker_get;assert docker_get("/version").get("Version")' >/dev/null 2>&1; then
    auto=1
    break
  fi
  sleep 2
done
if [[ "$auto" != 1 ]]; then
  echo "GUARD_AUTO_RESTART_TIMEOUT"
  docker inspect "$guard" --format 'Policy={{json .HostConfig.RestartPolicy}} RestartCount={{.RestartCount}} State={{json .State}}' || true
  echo "BeforeStarted=$before_started BeforePid=$before_pid AfterStarted=$after_started AfterPid=$after_pid"
  docker inspect "$workload" --format 'Workload={{json .State}}' || true
  docker exec --user 10001:10001 "$workload" python -c 'import os,stat; p="/run/oci-guard/guard.sock"; print("guard_socket_exists:",os.path.exists(p)); print("guard_socket_mode:",oct(stat.S_IMODE(os.stat(p).st_mode)) if os.path.exists(p) else "missing")' || true
  docker logs --tail 80 "$guard" || true
  exit 1
fi
docker update --restart no "$guard" >/dev/null
echo "GUARD_AUTOMATIC_CRASH_RECOVERY_OK"
docker stop --time 35 "$workload" >/dev/null
[[ "$(docker inspect "$workload" -f '{{.State.ExitCode}}')" == 0 ]] || exit 1
echo "WORKLOAD_GRACEFUL_STOP_OK"
echo "DISPOSABLE_EXTERNAL_GUARD_INTEGRATION_OK"
