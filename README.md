# OCI-Noah / OCI-N&T

OCI-Noah 是 OCI-N&T 的源码仓库。项目用于自托管的 Oracle Cloud Infrastructure（OCI）多账户管理，提供账户、实例、网络、存储、代理、监控、任务、审计等统一管理能力。

## 当前稳定版本

- 产品：OCI-N&T 2.0 Stable
- 版本：2.0.0
- Build：`v2.0.0-final-consolidation-r2`
- 后端：FastAPI / Python
- 前端：静态 Web UI / Nginx
- 数据库：SQLite
- 部署方式：Docker Compose
- 镜像仓库：GitHub Container Registry（GHCR）
- 生产服务器目录：`/opt/oci-nt`

> SQLite 数据库、运行日志、环境变量和密钥均属于运行时数据，不存储在 Git 仓库中。

## 主要功能

- OCI 多账户统一管理
- 每账户独立代理配置与代理健康检测
- 账户与实例信息本地缓存
- 实例开机、关机、重启等生命周期操作
- 自适应开机调度
- VNIC / IPv4 / IPv6 / 公网 IP 管理
- 启动卷与存储管理
- OCI Monitoring 指标查询
- VNC / Console 相关操作
- IAM 用户、组、策略管理
- Identity Domain 管理
- Region、配额与审计信息
- Cloudflare DNS 管理
- 任务中心
- 审计日志
- 本地系统监控

## 生产目录

```text
/opt/oci-nt
├── backend/
├── frontend/
├── deploy/
├── tools/
├── data/          # 运行数据，Git 忽略
├── logs/          # 运行日志，Git 忽略
├── .env           # 生产环境变量，Git 忽略
└── docker-compose.yml
```

## GitHub Actions 与 GHCR

项目使用 GitHub Actions 自动完成代码检查、测试和 Docker 镜像构建。

当代码进入 `main` 后，GitHub Actions 会构建并发布两个镜像：

```text
ghcr.io/noahting55/oci-noah-api
ghcr.io/noahting55/oci-noah-web
```

镜像标签包括：

```text
latest
sha-<完整 Git Commit SHA>
vX.Y.Z
```

生产部署优先使用不可变的：

```text
sha-<完整 Git Commit SHA>
```

这样可以确保运行中的 Docker 镜像与对应 Git Commit 完全一致，便于定位问题和回滚。

## GitHub → 生产服务器更新流程

正常发布流程：

```text
功能开发 / 修复
        ↓
GitHub 功能分支
        ↓
Pull Request
        ↓
GitHub Actions 自动测试
        ↓
合并到 main
        ↓
GitHub Actions 构建 API / Web 镜像
        ↓
推送到 GHCR
        ↓
生产服务器拉取对应 Commit 镜像
        ↓
滚动更新并执行健康检查
```

## 服务器首次登录 GHCR

仓库和镜像为私有资源时，生产服务器需要先登录 GHCR。

推荐使用只包含 `read:packages` 权限的 GitHub Token。

```bash
cd /opt/oci-nt
bash deploy/ghcr-login.sh
```

Token 不应写入项目文件，也不要提交到 Git。

## 生产服务器更新

Git SSH 与 GHCR 登录完成后，日常更新只需要：

```bash
cd /opt/oci-nt
bash deploy/update-from-github.sh
```

更新脚本会自动完成：

1. 检查当前 API 与容器健康状态；
2. 检查 Git 工作区是否干净；
3. 获取 `origin/main` 最新版本；
4. 无更新时直接退出，不生成无意义数据库备份；
5. 拒绝自动回退或处理已经分叉的 Git 历史；
6. 有更新时创建 SQLite 一致性备份；
7. 切换到新的 Git Commit；
8. 拉取与该 Commit 对应的 GHCR API / Web 镜像；
9. 按顺序滚动更新 API、Docker Guard、Monitor 和 Web；
10. 每个服务启动后执行健康检查；
11. 更新失败时自动尝试回滚源码和容器。

## 日常开发方式

建议所有功能修改都通过独立分支完成，不直接在生产服务器修改源码。

推荐流程：

```text
main
 └─ 功能分支
      └─ 修改代码
      └─ 自动测试
      └─ Pull Request
      └─ 合并 main
      └─ 自动构建 GHCR 镜像
```

生产服务器只负责拉取和运行经过 GitHub Actions 构建验证的镜像。

## 数据与安全

以下内容严禁提交到 GitHub：

- `.env`
- SQLite 数据库及 WAL / SHM 文件
- 数据库备份
- 运行日志
- OCI API 私钥
- SSH 私钥
- 代理账号与密码
- Cloudflare Token
- Telegram Bot Token
- Google OAuth Secret
- Session / Cookie 数据
- GitHub Token
- 其他任何生产环境密钥

仓库中的 `.gitignore` 已排除主要运行时敏感文件，但提交前仍应进行人工检查。

## 版本规则

- `2.0.x`：兼容性维护、Bug 修复和小范围优化
- `2.1.x`：新增兼容功能
- `3.x`：重大架构调整或不兼容升级

正式发布版本使用 Git Tag，例如：

```text
v2.0.0
v2.0.1
v2.1.0
```

已发布的 Tag 不应移动或覆盖。

## 当前部署原则

生产环境遵循以下原则：

- GitHub 为源码主仓库
- `main` 为当前维护主线
- GitHub Actions 负责测试和构建
- GHCR 保存正式 Docker 镜像
- 生产服务器不再本地构建 API / Web 镜像
- 生产容器使用 `sha-<commit>` 固定镜像
- SQLite 在升级前自动备份
- 服务采用滚动更新和健康检查
- 更新失败时保留回滚能力

---

OCI-Noah / OCI-N&T 为自用 OCI 管理项目。
