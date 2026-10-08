# Read-only release drain report

Run only after a separately authenticated/operator-approved workflow enters
persistent release maintenance. **This script does not activate maintenance.**

```bash
python3 deploy/release-drain-check.py --db /opt/oci-nt/data/oci-nt.db
```

The script uses a consistent SQLite read transaction and checks:
- integrity via SQLite quick_check;
- durable release maintenance flag is active;
- no nonterminal statuses remain in manual_tasks or launch_jobs;
- unknown task statuses fail closed.

Its output always includes `release_authorized: false`, even when the database
task tables are drained. It cannot detect currently executing direct OCI HTTP
requests, queued in-memory work, or unsynchronized worker processes. It does
not make changes, restart containers, or release/enter the maintenance state.

## Remaining work before an API upgrade

Implement a coordinated admission-and-drain workflow that tracks in-flight
OCI requests and background work across all processes, verify the gate
remains active, create a verified SQLite backup after draining, verify
candidate image compatibility, and require explicit deployment approval.
Do not treat `DB_TASKS_DRAINED` as permission to restart.


## Durable OCI operation leases (PR #31)

The read-only drain report now reads OCI HTTP **and** background task leases
from the same SQLite snapshot as the maintenance marker and task tables.
An outstanding lease prevents `db_task_drain_ready`, even when task rows are
terminal. Missing lease records mean zero **recorded** leases; malformed
records fail closed. Lease identifiers are not included in the output.

A successful result still **does not authorize backend restart**: legacy
processes, detached OCI SDK work, and other uninstrumented workers remain
outside the persistent lease registry. `release_authorized` stays false.
