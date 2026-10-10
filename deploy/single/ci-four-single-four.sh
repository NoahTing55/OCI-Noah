#!/usr/bin/env bash
# Dedicated GitHub-hosted ephemeral runner ONLY; forbidden on production.
set -Eeuo pipefail
[[ "${GITHUB_ACTIONS:-}" == "true" && "${RUNNER_ENVIRONMENT:-}" == "github-hosted" ]] || { echo "EPHEMERAL_GITHUB_RUNNER_ONLY"; exit 3; }
cd "$(dirname "$0")/../.."
[[ ! -e data/oci-nt.db ]] || { echo "REFUSE_EXISTING_DATABASE"; exit 3; }
DIR="$(mktemp -d)"
cleanup() {
  docker rm -f oci-nt-single-ci-switch >/dev/null 2>&1 || true
  docker compose -p oci-nt -f docker-compose.yml down --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$DIR"
}
trap cleanup EXIT
mkdir -p data logs
chmod 777 data logs
KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
cat > .env <<EOF
SECRET_KEY=ci-only-not-a-secret
CREDENTIAL_ENCRYPTION_KEY=$KEY
ADMIN_USERNAME=ci-admin
ADMIN_PASSWORD=ci-test-password
DOCKER_GID=$(stat -c %g /var/run/docker.sock)
OCI_NOAH_BACKEND_TAG=ci-switch
OCI_NOAH_WEB_TAG=ci-switch
EOF
export OCI_NOAH_BACKEND_TAG=ci-switch OCI_NOAH_WEB_TAG=ci-switch
docker build -t ghcr.io/noahting55/oci-noah-api:ci-switch backend
docker build -t ghcr.io/noahting55/oci-noah-web:ci-switch frontend
docker compose -p oci-nt -f docker-compose.yml up -d --no-build
wait_port() {
  local url="$1"
  for i in $(seq 1 50); do
    curl -fsS --max-time 3 "$url" >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "SERVICE_START_TIMEOUT: $url"
  docker compose -p oci-nt -f docker-compose.yml ps || true
  return 1
}
wait_port http://127.0.0.1:9859/
wait_port http://127.0.0.1:9859/api/v1/health
wait_port http://127.0.0.1:9860/health
wait_port http://127.0.0.1:9861/health
docker exec -i -u 10001 oci-nt-api python - <<'PY'
import sqlite3
with sqlite3.connect("/app/data/oci-nt.db") as c:
  assert c.execute("PRAGMA quick_check").fetchone()[0]=="ok"
  assert c.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]==7
  c.execute("CREATE TABLE ci_switch_marker (message TEXT NOT NULL)")
  c.execute("INSERT INTO ci_switch_marker VALUES ('four-before-single')")
PY
echo FOUR_CONTAINER_BASELINE_OK
docker compose -p oci-nt -f docker-compose.yml down
[[ -f data/oci-nt.db ]] || { echo DB_LOST_AFTER_FOUR_DOWN; exit 1; }
# Same disposable SQLite database; never connect to a production host.
docker run -d --name oci-nt-single-ci-switch --network host --restart no \
  --group-add "$(stat -c %g /var/run/docker.sock)" \
  -v "$PWD/data:/app/data" -v "$PWD/logs:/app/logs" \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e SECRET_KEY=ci-only-not-a-secret -e CREDENTIAL_ENCRYPTION_KEY="$KEY" \
  -e ADMIN_USERNAME=ci-admin -e ADMIN_PASSWORD=ci-test-password \
  -e DOCKER_GID="$(stat -c %g /var/run/docker.sock)" \
  -e DOCKER_GUARD_HOST=127.0.0.1 -e DOCKER_GUARD_PORT=9861 \
  -e MONITOR_AGENT_HOST=127.0.0.1 -e MONITOR_AGENT_PORT=9860 \
  oci-nt-single:ci
wait_port http://127.0.0.1:9859/api/v1/health
wait_port http://127.0.0.1:9860/health
docker exec oci-nt-single-ci-switch python /app/health.py
docker exec -i -u 10001 oci-nt-single-ci-switch python - <<'PY'
import sqlite3
with sqlite3.connect("/app/data/oci-nt.db") as c:
  assert c.execute("PRAGMA quick_check").fetchone()[0]=="ok"
  assert c.execute("SELECT message FROM ci_switch_marker").fetchone()[0]=="four-before-single"
  c.execute("INSERT INTO ci_switch_marker VALUES ('single-before-rollback')")
PY
echo FOUR_TO_SINGLE_OK
docker stop --time 35 oci-nt-single-ci-switch >/dev/null
docker rm oci-nt-single-ci-switch >/dev/null
docker compose -p oci-nt -f docker-compose.yml up -d --no-build
wait_port http://127.0.0.1:9859/api/v1/health
wait_port http://127.0.0.1:9860/health
wait_port http://127.0.0.1:9861/health
docker exec -i -u 10001 oci-nt-api python - <<'PY'
import sqlite3
with sqlite3.connect("/app/data/oci-nt.db") as c:
  assert c.execute("PRAGMA quick_check").fetchone()[0]=="ok"
  assert c.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]==7
  assert c.execute("SELECT message FROM ci_switch_marker ORDER BY rowid").fetchall()==[("four-before-single",),("single-before-rollback",)]
PY
echo SINGLE_TO_FOUR_ROLLBACK_OK
