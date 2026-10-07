# OCI-Noah / OCI-N&T

Self-hosted Oracle Cloud multi-account management console.

## Stable release

- Product: OCI-N&T 2.0 Stable
- Version: 2.0.0
- Build: `v2.0.0-final-consolidation-r2`
- Backend: FastAPI / Python
- Frontend: static Web UI / Nginx
- Database: SQLite (runtime-only; not stored in Git)
- Deployment: Docker Compose

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

## Update from GitHub

After GitHub authentication is configured:

```bash
cd /opt/oci-nt
bash deploy/update-from-github.sh
```

## Versioning

- 2.0.x: compatible maintenance fixes
- 2.1.x: new compatible features
- 3.x: major architectural changes
