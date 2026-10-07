#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="/opt/oci-nt"
BRANCH="${1:-main}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DB="$PROJECT/data/oci-nt.db"
DB_BACKUP="$PROJECT/data/backups/pre-git-update-${STAMP}.db"

log(){ printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "请使用 root 执行"
cd "$PROJECT"
[[ -d .git ]] || fail "当前目录尚未初始化 Git"
[[ -f .env ]] || fail "缺少运行环境 .env"

log "1. 当前服务健康检查"
curl -fsS --max-time 5 http://127.0.0.1:9858/api/v1/health
echo

for c in oci-nt-api oci-nt-docker-guard oci-nt-monitor-agent oci-nt-web; do
  s="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" 2>/dev/null || true)"
  [[ "$s" == "healthy" ]] || fail "$c 当前不是 healthy"
done

log "2. 检查工作区"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  git status --short
  fail "存在未提交的已跟踪修改，拒绝自动更新"
fi

OLD_HEAD="$(git rev-parse HEAD)"
echo "OLD_HEAD=$OLD_HEAD"

log "3. SQLite 一致性备份"
mkdir -p "$PROJECT/data/backups"
python3 - "$DB" "$DB_BACKUP" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
a = sqlite3.connect(src)
b = sqlite3.connect(dst)
with b:
    a.backup(b)
a.close()
check = b.execute("PRAGMA quick_check").fetchone()[0]
b.close()
print("quick_check =", check)
if check != "ok":
    raise SystemExit(1)
PY
echo "DB_BACKUP=$DB_BACKUP"

rollback() {
  rc=$?
  trap - ERR
  echo "更新失败，回滚到 $OLD_HEAD"
  git reset --hard "$OLD_HEAD" || true
  docker compose build api || true
  docker compose build web || true
  docker compose up -d --force-recreate api docker-guard monitor web || true
  exit "$rc"
}
trap rollback ERR

log "4. 获取 GitHub 更新"
git fetch --prune origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
NEW_HEAD="$(git rev-parse HEAD)"
echo "NEW_HEAD=$NEW_HEAD"

if [[ "$OLD_HEAD" == "$NEW_HEAD" ]]; then
  echo "已经是最新版本。"
  trap - ERR
  exit 0
fi

log "5. 源码检查"
python3 -m compileall -q backend/app
if command -v node >/dev/null 2>&1; then
  node --check frontend/app.js
  node --check frontend/rc.js
  node --check frontend/ui2.js
fi

log "6. 顺序构建 API"
docker compose build api
log "7. 顺序构建 Web"
docker compose build web

wait_health() {
  local c="$1"
  for _ in $(seq 1 45); do
    local s
    s="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" 2>/dev/null || true)"
    [[ "$s" == "healthy" ]] && { echo "$c HEALTHY"; return 0; }
    [[ "$s" == "unhealthy" || "$s" == "exited" || "$s" == "dead" ]] && return 1
    sleep 2
  done
  return 1
}

log "8. 滚动替换 API"
docker compose up -d --no-deps --force-recreate api
wait_health oci-nt-api

log "9. 滚动替换 Guard / Monitor"
docker compose up -d --no-deps --force-recreate docker-guard
wait_health oci-nt-docker-guard
docker compose up -d --no-deps --force-recreate monitor
wait_health oci-nt-monitor-agent

log "10. 滚动替换 Web"
docker compose up -d --no-deps --force-recreate web
wait_health oci-nt-web

log "11. 最终验证"
curl -fsS --max-time 5 http://127.0.0.1:9858/api/v1/health
echo
docker compose ps

trap - ERR
echo "GITHUB_UPDATE_OK"
echo "OLD_HEAD=$OLD_HEAD"
echo "NEW_HEAD=$NEW_HEAD"
echo "DB_BACKUP=$DB_BACKUP"
