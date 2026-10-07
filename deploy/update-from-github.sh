#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="/opt/oci-nt"
BRANCH="${1:-main}"
STAMP="$(date +%Y%m%d-%H%M%S)"

DB="$PROJECT/data/oci-nt.db"
BACKUP_DIR="$PROJECT/data/backups"
DB_BACKUP="$BACKUP_DIR/pre-git-update-${STAMP}.db"

log() {
    printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "请使用 root 执行"

cd "$PROJECT"

[[ -d .git ]] || fail "当前目录尚未初始化 Git"
[[ -f .env ]] || fail "缺少运行环境 .env"
[[ -f "$DB" ]] || fail "数据库不存在：$DB"

export GIT_TERMINAL_PROMPT=0

log "1. 当前服务健康检查"

curl -fsS --max-time 5     http://127.0.0.1:9858/api/v1/health
echo

for c in     oci-nt-api     oci-nt-docker-guard     oci-nt-monitor-agent     oci-nt-web
do
    s="$(
        docker inspect             -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'             "$c" 2>/dev/null || true
    )"

    printf '%-24s %s\n' "$c" "$s"

    [[ "$s" == "healthy" ]]         || fail "$c 当前不是 healthy"
done

log "2. 检查工作区"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    git status --short
    fail "存在未提交的已跟踪修改，拒绝自动更新"
fi

CURRENT_BRANCH="$(git branch --show-current)"

[[ "$CURRENT_BRANCH" == "$BRANCH" ]]     || fail "当前分支为 $CURRENT_BRANCH，不是目标分支 $BRANCH"

OLD_HEAD="$(git rev-parse HEAD)"

echo "BRANCH=$BRANCH"
echo "OLD_HEAD=$OLD_HEAD"

log "3. 获取 GitHub 最新状态"

git fetch --prune origin

git rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1     || fail "远端不存在 origin/$BRANCH"

REMOTE_HEAD="$(git rev-parse "origin/$BRANCH")"

echo "LOCAL_HEAD =$OLD_HEAD"
echo "REMOTE_HEAD=$REMOTE_HEAD"

if [[ "$OLD_HEAD" == "$REMOTE_HEAD" ]]; then
    echo
    echo "已经是最新版本。"
    echo "NO_UPDATE"
    exit 0
fi

if git merge-base --is-ancestor "$OLD_HEAD" "$REMOTE_HEAD"; then
    echo "UPDATE_DIRECTION=FORWARD"
elif git merge-base --is-ancestor "$REMOTE_HEAD" "$OLD_HEAD"; then
    fail "本地代码领先 GitHub，拒绝自动回退"
else
    fail "本地与 GitHub 分支已经分叉，拒绝自动更新"
fi

log "4. 检测到新版本"

echo "FROM=$OLD_HEAD"
echo "TO  =$REMOTE_HEAD"
echo
echo "更新内容："

GIT_PAGER=cat git log     --oneline     --decorate     "${OLD_HEAD}..${REMOTE_HEAD}" || true

log "5. SQLite 一致性备份"

mkdir -p "$BACKUP_DIR"

python3 - "$DB" "$DB_BACKUP" <<'PY'
import sqlite3
import sys

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

    echo
    echo "=========================================="
    echo "更新失败，开始源码/容器回滚"
    echo "OLD_HEAD=$OLD_HEAD"
    echo "=========================================="

    git reset --hard "$OLD_HEAD" || true

    if grep -q 'OCI_NOAH_IMAGE_TAG' docker-compose.yml 2>/dev/null; then
        export OCI_NOAH_IMAGE_TAG="sha-$OLD_HEAD"
        echo "ROLLBACK_IMAGE_TAG=$OCI_NOAH_IMAGE_TAG"
        docker compose pull api web || true
    else
        unset OCI_NOAH_IMAGE_TAG || true
        echo "ROLLBACK_MODE=legacy-local-image"
    fi

    docker compose up -d         --force-recreate         api docker-guard monitor web || true

    echo
    echo "数据库升级前备份仍保留："
    echo "$DB_BACKUP"

    exit "$rc"
}

trap rollback ERR

log "6. 切换到 GitHub 新版本"

git reset --hard "$REMOTE_HEAD"

NEW_HEAD="$(git rev-parse HEAD)"
NEW_IMAGE_TAG="sha-$NEW_HEAD"

echo "NEW_HEAD=$NEW_HEAD"
echo "NEW_IMAGE_TAG=$NEW_IMAGE_TAG"

log "7. 源码检查"

python3 -m compileall -q backend/app

if command -v node >/dev/null 2>&1; then
    [[ -f frontend/app.js ]] && node --check frontend/app.js
    [[ -f frontend/rc.js ]] && node --check frontend/rc.js
    [[ -f frontend/ui2.js ]] && node --check frontend/ui2.js
fi

export OCI_NOAH_IMAGE_TAG="$NEW_IMAGE_TAG"

docker compose config >/dev/null

log "8. 等待并拉取 GHCR 镜像"

PULL_OK=0

for attempt in $(seq 1 20); do
    echo "GHCR_PULL_ATTEMPT=$attempt/20"

    if docker compose pull api web; then
        PULL_OK=1
        break
    fi

    echo "镜像可能仍在 GitHub Actions 构建中，15 秒后重试..."
    sleep 15
done

[[ "$PULL_OK" -eq 1 ]]     || fail "等待 GHCR 镜像超时，请检查 GitHub Actions"

wait_health() {
    local container="$1"

    for _ in $(seq 1 60); do
        local state

        state="$(
            docker inspect                 -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'                 "$container" 2>/dev/null || true
        )"

        if [[ "$state" == "healthy" ]]; then
            echo "$container HEALTHY"
            return 0
        fi

        if [[             "$state" == "unhealthy" ||             "$state" == "exited" ||             "$state" == "dead"         ]]; then
            echo "$container STATE=$state"
            return 1
        fi

        sleep 2
    done

    echo "$container HEALTH_TIMEOUT"
    return 1
}

log "9. 滚动替换 API"

docker compose up -d     --no-deps     --force-recreate     api

wait_health oci-nt-api

log "10. 滚动替换 Docker Guard"

docker compose up -d     --no-deps     --force-recreate     docker-guard

wait_health oci-nt-docker-guard

log "11. 滚动替换 Monitor"

docker compose up -d     --no-deps     --force-recreate     monitor

wait_health oci-nt-monitor-agent

log "12. 滚动替换 Web"

docker compose up -d     --no-deps     --force-recreate     web

wait_health oci-nt-web

log "13. 最终 API 验证"

curl -fsS --max-time 5     http://127.0.0.1:9858/api/v1/health
echo

docker compose ps

log "14. 清理旧 Git 更新备份"

find "$BACKUP_DIR"     -maxdepth 1     -type f     -name 'pre-git-update-*.db'     -printf '%T@ %p\n'     | sort -nr     | tail -n +11     | cut -d' ' -f2-     | xargs -r rm -f

trap - ERR

echo
echo "=========================================="
echo "OCI-Noah GHCR_UPDATE_OK"
echo "OLD_HEAD=$OLD_HEAD"
echo "NEW_HEAD=$NEW_HEAD"
echo "IMAGE_TAG=$NEW_IMAGE_TAG"
echo "DB_BACKUP=$DB_BACKUP"
echo "=========================================="
