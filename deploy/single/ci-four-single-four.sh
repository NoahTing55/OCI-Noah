#!/usr/bin/env bash
# Dedicated GitHub-hosted ephemeral runner ONLY; forbidden on production.
set -Eeuo pipefail
[[ "${GITHUB_ACTIONS:-}" == "true" && "${RUNNER_ENVIRONMENT:-}" == "github-hosted" ]] || { echo "EPHEMERAL_GITHUB_RUNNER_ONLY"; exit 3; }
cd "$(dirname "$0")/../.."
[[ ! -e data/oci-nt.db ]] || { echo "REFUSE_EXISTING_DATABASE"; exit 3; }
DIR="$(mktemp -d)"
cleanup() {
  docker rm -f oci-nt-single-ci-switch oci-nt-single-ci-failed >/dev/null 2>&1 || true
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
# Compare actual HTTP-served UI bytes through every deployment generation.
# This catches stale JS/CSS, missing VNC pages, or mismatched icons despite green health.
STATIC_ASSETS=(/ /app.js /rc.js /ui2.js /styles.css /ui-theme.css /vnc.html /favicon.svg /favicon.ico /apple-touch-icon.png)
capture_api_contract() {
  curl --fail --silent --show-error --max-time 15 http://127.0.0.1:9858/api/openapi.json |
    python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"paths":d.get("paths",{}),"components":d.get("components",{})},sort_keys=True,separators=(",",":")))'
}
capture_ui_hashes() {
  local asset
  for asset in "${STATIC_ASSETS[@]}"; do
    printf '%s ' "$asset"
    curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:9859${asset}" | sha256sum | cut -d' ' -f1
  done
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
  from app.credential_crypto import encrypt_secret, decrypt_secret
  token=encrypt_secret("ci-synthetic-oci-secret")
  assert decrypt_secret(token)=="ci-synthetic-oci-secret"
  c.execute("CREATE TABLE ci_encrypted_marker (secret TEXT NOT NULL)")
  c.execute("INSERT INTO ci_encrypted_marker VALUES (?)",(token,))
PY
capture_ui_hashes > "$DIR/four-ui.sha256"
capture_api_contract > "$DIR/four-api.json"
echo SYNTHETIC_ENCRYPTED_CREDENTIAL_CREATED_OK
echo FOUR_CONTAINER_BASELINE_OK
docker compose -p oci-nt -f docker-compose.yml down
[[ -f data/oci-nt.db ]] || { echo DB_LOST_AFTER_FOUR_DOWN; exit 1; }
# A stopped, immutable point-in-time rollback snapshot is captured before any
# candidate boots. Use SQLite backup API; verify both full integrity and
# synthetic credential marker in a separately opened read-only connection.
python3 - "$PWD/data/oci-nt.db" "$DIR/stopped-four-backup.db" <<'PY'
import sqlite3,sys
from pathlib import Path
source=Path(sys.argv[1]); backup=Path(sys.argv[2])
assert source.is_file() and not source.is_symlink()
assert not backup.exists()
with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro",uri=True) as src:
    assert src.execute("PRAGMA quick_check").fetchone()[0]=="ok"
    assert src.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]==7
    with sqlite3.connect(backup) as dst:
        src.backup(dst)
with sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro",uri=True) as dst:
    assert dst.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert dst.execute("SELECT version FROM schema_version WHERE singleton_id=1").fetchone()[0]==7
    assert dst.execute("SELECT message FROM ci_switch_marker").fetchone()[0]=="four-before-single"
    assert dst.execute("SELECT COUNT(*) FROM ci_encrypted_marker").fetchone()[0]==1
PY
chmod 0400 "$DIR/stopped-four-backup.db"
# sqlite3.connect(backup) is isolated, but host-run SQLite access may create
# journal sidecars in the source directory. Explicitly assert app UID ownership
# and writable SQLite/WAL state before restarting the old API in CI.
docker run --rm --network none --user 0:0 \
  -v "$PWD/data:/app/data" --entrypoint /bin/sh \
  ghcr.io/noahting55/oci-noah-api:ci-switch -c \
  'chown 10001:10001 /app/data/oci-nt.db /app/data/oci-nt.db-wal /app/data/oci-nt.db-shm 2>/dev/null || chown 10001:10001 /app/data/oci-nt.db'
docker run --rm --network none --user 10001:10001 \
  -v "$PWD/data:/app/data" --entrypoint python \
  ghcr.io/noahting55/oci-noah-api:ci-switch -c \
  'import sqlite3; c=sqlite3.connect("/app/data/oci-nt.db"); c.execute("BEGIN IMMEDIATE"); c.rollback(); c.close()'
echo STOPPED_FOUR_DB_APP_UID_WRITE_PROBE_OK
echo STOPPED_FOUR_SQLITE_BACKUP_VERIFIED_OK
# Failure-injection rollback: candidate with invalid socket group must fail closed.
# Never try this on production; CI guard at script entry is mandatory.
docker run -d --name oci-nt-single-ci-failed --network host --restart no \
  -e DOCKER_GID=0 -e SECRET_KEY=ci-only-not-a-secret \
  -e CREDENTIAL_ENCRYPTION_KEY="$KEY" \
  oci-nt-single:ci >/dev/null
for i in $(seq 1 20); do
  [[ "$(docker inspect -f '{{.State.Running}}' oci-nt-single-ci-failed)" == "false" ]] && break
  sleep 1
done
[[ "$(docker inspect -f '{{.State.Running}}' oci-nt-single-ci-failed)" == "false" ]] || { echo FAILURE_INJECTION_NOT_REJECTED; exit 1; }
docker rm -f oci-nt-single-ci-failed >/dev/null
if ! docker compose -p oci-nt -f docker-compose.yml up -d --no-build; then
  echo "FOUR_CONTAINER_RECOVERY_START_FAILED"
  docker compose -p oci-nt -f docker-compose.yml ps || true
  docker logs --tail 120 oci-nt-api || true
  ls -lan data/oci-nt.db* || true
  exit 1
fi
wait_port http://127.0.0.1:9859/api/v1/health
wait_port http://127.0.0.1:9860/health
wait_port http://127.0.0.1:9861/health
docker exec -i -u 10001 oci-nt-api python - <<'PY'
import sqlite3
with sqlite3.connect("/app/data/oci-nt.db") as c:
  assert c.execute("PRAGMA quick_check").fetchone()[0]=="ok"
  assert c.execute("SELECT message FROM ci_switch_marker").fetchone()[0]=="four-before-single"
  from app.credential_crypto import decrypt_secret
  from cryptography.fernet import Fernet,InvalidToken
  token=c.execute("SELECT secret FROM ci_encrypted_marker").fetchone()[0]
  assert decrypt_secret(token)=="ci-synthetic-oci-secret"
  try:
    Fernet(Fernet.generate_key()).decrypt(token.encode("ascii"))
  except InvalidToken:
    pass
  else:
    raise AssertionError("wrong key unexpectedly decrypted synthetic secret")
PY
echo FAILED_SINGLE_TO_FOUR_RECOVERY_OK
docker compose -p oci-nt -f docker-compose.yml down
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
  from app.credential_crypto import decrypt_secret
  from cryptography.fernet import Fernet,InvalidToken
  token=c.execute("SELECT secret FROM ci_encrypted_marker").fetchone()[0]
  assert decrypt_secret(token)=="ci-synthetic-oci-secret"
  try:
    Fernet(Fernet.generate_key()).decrypt(token.encode("ascii"))
  except InvalidToken:
    pass
  else:
    raise AssertionError("wrong key unexpectedly decrypted synthetic secret")
  c.execute("INSERT INTO ci_switch_marker VALUES ('single-before-rollback')")
PY
capture_ui_hashes > "$DIR/single-ui.sha256"
capture_api_contract > "$DIR/single-api.json"
diff -u "$DIR/four-api.json" "$DIR/single-api.json"
echo SINGLE_CONTAINER_API_CONTRACT_PARITY_OK
diff -u "$DIR/four-ui.sha256" "$DIR/single-ui.sha256"
echo SINGLE_CONTAINER_UI_PARITY_OK
echo SINGLE_CONTAINER_SYNTHETIC_CREDENTIAL_READ_OK
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
  from app.credential_crypto import decrypt_secret
  token=c.execute("SELECT secret FROM ci_encrypted_marker").fetchone()[0]
  assert decrypt_secret(token)=="ci-synthetic-oci-secret"
PY
capture_ui_hashes > "$DIR/restored-ui.sha256"
capture_api_contract > "$DIR/restored-api.json"
diff -u "$DIR/four-api.json" "$DIR/restored-api.json"
echo FOUR_CONTAINER_API_ROLLBACK_PARITY_OK
diff -u "$DIR/four-ui.sha256" "$DIR/restored-ui.sha256"
echo FOUR_CONTAINER_UI_ROLLBACK_PARITY_OK
# Original backup stays readable and unchanged even after candidate writes.
python3 - "$DIR/stopped-four-backup.db" <<'PY'
import sqlite3,sys
from pathlib import Path
db=Path(sys.argv[1])
assert db.stat().st_mode & 0o777 == 0o400
with sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro",uri=True) as con:
    assert con.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert con.execute("SELECT message FROM ci_switch_marker ORDER BY rowid").fetchall()==[("four-before-single",)]
    assert con.execute("SELECT COUNT(*) FROM ci_encrypted_marker").fetchone()[0]==1
PY
echo STOPPED_FOUR_BACKUP_RETAINED_AFTER_ROLLBACK_OK
echo FOUR_CONTAINER_SYNTHETIC_CREDENTIAL_ROLLBACK_OK
echo SINGLE_TO_FOUR_ROLLBACK_OK
