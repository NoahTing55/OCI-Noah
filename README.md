# OCI-Noah / OCI-N&T

Self-hosted Oracle Cloud multi-account management console.

## Stable release

- Product: OCI-N&T 2.0 Stable
- Version: 2.0.0
- Build: `v2.0.0-final-consolidation-r2`
- Backend: FastAPI / Python
- Frontend: static Web UI / Nginx
- Database: SQLite (runtime-only; not stored in Git)
- Deployment: Docker Compose + GitHub Container Registry

## Main capabilities

- Multi-account OCI management
- Per-account proxy configuration and health checks
- Cache-first instance/account inventory
- Instance lifecycle operations
- Adaptive launch scheduler
- VNIC / IPv4 / IPv6 / public-IP management
- Boot-volume and storage management
- OCI Monitoring metrics
- VNC / console workflows
- IAM / Identity Domain management
- Region / quota / audit views
- Cloudflare DNS integration
- Task center, audit logs and local monitoring

## Security

Never commit `.env`, runtime databases, logs, OCI private keys, proxy credentials,
Cloudflare tokens, Telegram tokens, Google OAuth secrets, session data or backups.

## Production directory

```text
/opt/oci-nt
├── backend/
├── frontend/
├── deploy/
├── tools/
├── data/          # ignored
├── logs/          # ignored
├── .env           # ignored
└── docker-compose.yml
```

## CI and image publishing

Pushes to `main` run the source checks and publish two GHCR images:

```text
ghcr.io/noahting55/oci-noah-api
ghcr.io/noahting55/oci-noah-web
```

Published tags include:

```text
latest
sha-<full git commit sha>
vX.Y.Z
```

Production deployment uses the immutable `sha-<commit>` tag, so the running
container images always match the exact Git commit being deployed.

## One-time GHCR login on the server

The repository is private, so the production server must authenticate to GHCR once.
Use a GitHub token that only needs package read permission:

```bash
cd /opt/oci-nt
bash deploy/ghcr-login.sh
```

The token is never stored in this Git repository.

## Update from GitHub

After Git SSH authentication and GHCR authentication are configured:

```bash
cd /opt/oci-nt
bash deploy/update-from-github.sh
```

The updater:

1. checks the current services and Git worktree;
2. fetches `origin/main`;
3. exits without a database backup when there is no update;
4. refuses backward or diverged Git histories;
5. creates a consistent SQLite backup only when an update exists;
6. pulls the exact `sha-<commit>` API and Web images from GHCR;
7. rolls services forward one by one with health checks;
8. rolls back the source and containers if deployment fails.

## Versioning

- 2.0.x: compatible maintenance fixes
- 2.1.x: new compatible features
- 3.x: major architectural changes
