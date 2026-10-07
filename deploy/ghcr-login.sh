#!/usr/bin/env bash
set -Eeuo pipefail

REGISTRY="ghcr.io"
USERNAME="${1:-NoahTing55}"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: 请使用 root 执行" >&2
    exit 1
fi

echo "GitHub Container Registry 私有镜像登录"
echo "用户名: $USERNAME"
echo
echo "请输入仅包含 read:packages 权限的 GitHub Token。"
echo "输入内容不会显示，也不会写入项目文件。"
echo

read -rsp "GHCR Token: " GHCR_TOKEN
echo

if [[ -z "$GHCR_TOKEN" ]]; then
    echo "ERROR: Token 为空" >&2
    exit 1
fi

printf '%s' "$GHCR_TOKEN"     | docker login "$REGISTRY"         --username "$USERNAME"         --password-stdin

unset GHCR_TOKEN

chmod 700 /root/.docker 2>/dev/null || true
chmod 600 /root/.docker/config.json 2>/dev/null || true

echo
echo "GHCR_LOGIN_OK"
