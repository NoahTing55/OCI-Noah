# Backend release safety — phase 2 (preflight only)

This phase deliberately **does not deploy backend images**, restart the API/monitor/guard,
or migrate/restore the database. It adds a read-only database/task gate before any
future backend deployment tooling can be considered.

Run on the production host:

```bash
cd /opt/oci-nt
python3 deploy/backend-preflight.py --db /opt/oci-nt/data/oci-nt.db
```

Exit codes: 0 = no active/unknown task statuses found in both tables and SQLite
quick_check passed; 2 = database/table/integrity errors; 3 = nonterminal work
exists. Fail closed: unknown statuses are treated as active.

A passing preflight is **not enough to restart a live backend**: new scheduled tasks
may start immediately after the query. Before implementing `--apply`:
1. Add a durable maintenance gate that blocks all new jobs/schedulers across the API.
2. Wait for or explicitly cancel in-progress operations; verify twice while gated.
3. Create and verify a consistent SQLite backup (SQLite backup API, not `cp` of a WAL database).
4. Stage immutable API/worker image; check startup/schema compatibility.
5. Preserve backups for human-approved DB rollback; never blindly restore a
   potentially migrated DB over live writes.
6. Require explicit approval and audit the deployment/rollback.

The preflight is informational for now; use the existing Web-only release
procedure for front-end deployments.
