#!/usr/bin/env bash
# GitHub-hosted ephemeral runner ONLY. Never invoke against a production host.
set -Eeuo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo "CI_ONLY"; exit 3; }
ID="oci-nt-single-ci-${GITHUB_RUN_ID:-local}"
dir="$(mktemp -d)"
cleanup() { docker rm -f "$ID" >/dev/null 2>&1 || true; rm -rf "$dir"; }
trap cleanup EXIT
mkdir -p "$dir/data" "$dir/logs"
chmod 777 "$dir/data" "$dir/logs"
# No OCI credentials, no copied production SQLite, no prod bind mount.
KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
docker run -d --name "$ID" --network host --restart no \
  --group-add "$(stat -c %g /var/run/docker.sock)" \
  -v "$dir/data:/app/data" -v "$dir/logs:/app/logs" \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e SECRET_KEY=ci-only-not-a-secret \
  -e CREDENTIAL_ENCRYPTION_KEY="$KEY" \
  -e ADMIN_USERNAME=ci-admin -e ADMIN_PASSWORD=ci-test-password \
  oci-nt-single:ci
alive=0
for i in $(seq 1 45); do
  if [[ "$(docker inspect "$ID" -f '{{.State.Running}}')" != true ]]; then
    docker logs "$ID" || true
    echo "CONTAINER_EXITED_EARLY"
    exit 1
  fi
  if docker exec "$ID" python /app/health.py; then alive=1; break; fi
  sleep 2
done
[[ "$alive" == 1 ]] || { docker logs "$ID"; echo "HEALTH_TIMEOUT"; exit 1; }
curl --fail --silent --show-error http://127.0.0.1:9859/ui-theme.css >/dev/null
curl --fail --silent --show-error http://127.0.0.1:9859/api/v1/health >/dev/null
echo "SINGLE_CONTAINER_RUNTIME_HEALTHY"
# Crash one critical child; supervisor must stop the whole container, not leave partial service alive.
docker exec "$ID" python -c 'import os,signal; found=[]; [(found.append(int(p))) for p in os.listdir("/proc") if p.isdigit() and os.path.exists("/proc/"+p+"/cmdline") and b"uvicorn\x00app.main:app" in open("/proc/"+p+"/cmdline","rb").read()]; assert len(found)==1,found; os.kill(found[0],signal.SIGKILL)'
status=running
for i in $(seq 1 20); do
  status="$(docker inspect "$ID" -f '{{.State.Status}}')"
  [[ "$status" == exited ]] && break
  sleep 1
done
[[ "$status" == exited ]] || { echo "FAIL_FAST_NOT_WORKING: $status"; docker logs "$ID"; exit 1; }
echo "SINGLE_CONTAINER_FAIL_FAST_OK"
