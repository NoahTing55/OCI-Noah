#!/usr/bin/env bash
# Four legacy containers -> pinned external Guard RC -> four legacy containers.
# CI-only, disposable synthetic Schema 7 data; never execute on a VPS.
set -Eeuo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted && -n "${GITHUB_RUN_ID:-}" ]] || exit 3
[[ "$PWD" == "${GITHUB_WORKSPACE:-}" ]] || exit 3
T="$(mktemp -d)"
mkdir -p "$T/src"
# All relative Compose paths resolve inside a fresh disposable copy.
tar --exclude=.git --exclude=data --exclude=logs --exclude=.env -cf - . | (cd "$T/src" && tar -xf -)
cd "$T/src"
[[ ! -e data/oci-nt.db ]] || { echo EXISTING_DB_REFUSED; exit 3; }
mkdir -p data logs
chmod 777 data logs
PROJECT="oci-nt-rollback-rc-${GITHUB_RUN_ID}"
OLD="docker compose -p $PROJECT -f docker-compose.yml"
NEW="docker compose -p ${PROJECT}-new -f deploy/compose.external-guard-rc.review.yml"
cleanup() {
  $NEW down -v --remove-orphans >/dev/null 2>&1 || true
  $OLD down --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$T"
}
trap cleanup EXIT
KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
cat > .env <<EOF
SECRET_KEY=ci-disposable-only
CREDENTIAL_ENCRYPTION_KEY=$KEY
ADMIN_USERNAME=ci-admin
ADMIN_PASSWORD=ci-password
ACCESS_TOKEN_MINUTES=60
BULK_CHECK_INTERVAL_SECONDS=120
DOCKER_GID=$(stat -c %g /var/run/docker.sock)
EOF
export DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
export OCI_NOAH_BACKEND_TAG=ci-switch OCI_NOAH_WEB_TAG=ci-switch
export OCI_NT_RC_ENV_FILE="$PWD/.env"
export OCI_NT_RC_DATA_DIR="$PWD/data"
export OCI_NT_RC_LOG_DIR="$PWD/logs"
[[ "$DOCKER_GID" =~ ^[0-9]+$ && "$DOCKER_GID" -gt 0 ]] || exit 3
docker build -q -t ghcr.io/noahting55/oci-noah-api:ci-switch backend
docker build -q -t ghcr.io/noahting55/oci-noah-web:ci-switch frontend
# Validate the rendered candidate topology before touching any CI containers.
$NEW config --format json | python deploy/single/validate-external-guard-rc.py
wait_http() {
  local url="$1"
  for _ in $(seq 1 65); do
    curl -fsS --max-time 3 "$url" >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "HEALTH_TIMEOUT $url"
  echo "CANDIDATE_COMPOSE_STATUS"
  $NEW ps -a || true
  echo "CANDIDATE_LOGS"
  $NEW logs --no-color --tail=120 || true
  echo "OLD_COMPOSE_STATUS"
  $OLD ps -a || true
  echo "PORT_LISTENERS"
  ss -lntp | grep -E ':(9858|9859|9860|9861)[[:space:]]' || true
  echo "CI_DATABASE_PERMISSIONS"
  ls -ldn data logs data/oci-nt.db* || true
  return 1
}
capture_contract() {
  curl -fsS --max-time 10 http://127.0.0.1:9858/api/openapi.json |
    python3 -c 'import json,sys,hashlib; d=json.load(sys.stdin); print(hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest())'
}
$OLD up -d --no-build
wait_http http://127.0.0.1:9859/api/v1/health
wait_http http://127.0.0.1:9860/health
wait_http http://127.0.0.1:9861/health
capture_contract > "$T/original-api.sha"
docker exec -i --user 10001:10001 oci-nt-api python - <<'PY'
import sqlite3
from app.credential_crypto import encrypt_secret
with sqlite3.connect("/app/data/oci-nt.db") as d:
 assert d.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
 assert d.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]==7
 d.execute("CREATE TABLE ci_rollback_marker (stage TEXT, credential TEXT)")
 d.execute("INSERT INTO ci_rollback_marker VALUES (?,?)",("four-before",encrypt_secret("ci-only-secret")))
PY
echo OLD_FOUR_CONTAINER_BASELINE_OK
$OLD down
python3 - "$PWD/data/oci-nt.db" "$T/rollback.db" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as src,sqlite3.connect(sys.argv[2]) as dst: src.backup(dst)
with sqlite3.connect(sys.argv[2]) as db:
 assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
 assert db.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]==7
 assert db.execute("SELECT stage FROM ci_rollback_marker").fetchone()[0]=="four-before"
PY
chmod 0400 "$T/rollback.db"
echo STOPPED_SCHEMA7_BACKUP_VERIFIED
# Candidate is always pinned and never receives the raw host Docker socket.
$NEW up -d --no-build
wait_http http://127.0.0.1:9859/api/v1/health
wait_http http://127.0.0.1:9860/health
candidate="$($NEW ps -q all-in-one)"
[[ -n "$candidate" ]] || exit 1
docker exec -i --user 10001:10001 "$candidate" python - <<'PY'
import sqlite3
from pathlib import Path
from app.credential_crypto import decrypt_secret
assert not Path("/var/run/docker.sock").exists()
with sqlite3.connect("/app/data/oci-nt.db") as d:
 assert d.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
 assert d.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]==7
 token=d.execute("SELECT credential FROM ci_rollback_marker WHERE stage='four-before'").fetchone()[0]
 assert decrypt_secret(token)=="ci-only-secret"
 d.execute("INSERT INTO ci_rollback_marker(stage) VALUES ('new-before-rollback')")
PY
capture_contract > "$T/new-api.sha"
diff -u "$T/original-api.sha" "$T/new-api.sha"
echo PINNED_NEW_TWO_CONTAINER_SCHEMA7_API_OK
# Simulate failed new generation by stopping its whole isolated project.
$NEW down -v --remove-orphans
$OLD up -d --no-build
wait_http http://127.0.0.1:9859/api/v1/health
wait_http http://127.0.0.1:9860/health
wait_http http://127.0.0.1:9861/health
diff -u "$T/original-api.sha" <(capture_contract)
docker exec -i --user 10001:10001 oci-nt-api python - <<'PY'
import sqlite3
from app.credential_crypto import decrypt_secret
with sqlite3.connect("/app/data/oci-nt.db") as db:
 assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
 assert db.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]==7
 rows=db.execute("SELECT stage,credential FROM ci_rollback_marker ORDER BY rowid").fetchall()
 assert [r[0] for r in rows]==["four-before","new-before-rollback"],rows
 assert decrypt_secret(rows[0][1])=="ci-only-secret"
PY
python3 - "$T/rollback.db" <<'PY'
import sqlite3,sys
with sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True) as d:
 assert d.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
 assert d.execute("SELECT COUNT(*) FROM ci_rollback_marker").fetchone()[0]==1
PY
echo PINNED_TWO_TO_FOUR_ROLLBACK_SCHEMA7_CREDENTIAL_API_OK
