# Backend release — verified backup (stage 2B)

This PR supplies a verified backup **building block only**. It does not enable API
maintenance mode, block scheduled starts, deploy a backend image, or authorize a restart.

After CI passes and the changes are merged, a privileged operator may explicitly
create a consistent SQLite snapshot on the production host:

```bash
cd /opt/oci-nt
python3 deploy/backend-preflight.py --db data/oci-nt.db
python3 deploy/verified-release-backup.py \
  --db data/oci-nt.db \
  --output /root/oci-nt-backups/release-snapshots
```

The backup uses SQLite's online Backup API (WAL-safe), verifies `PRAGMA quick_check`,
records SHA-256 in a separate JSON manifest and does **not** call automatic backup
retention/cleanup. Store the snapshot directory securely: backups contain
account metadata, encrypted credential material and other private records.

A task preflight and online backup do **not** imply it is safe to restart the API:
new tasks and schedules can begin between them. Before implementing automatic
backend deployment, integrate a persistent, server-enforced maintenance gate
across manual/scheduled task creators and workers, wait for all active jobs,
re-check task state while gated, and define migration-specific rollback with
operator approval. Avoid blind restoration of SQLite files over live writes.
