# Durable leases for OCI background asyncio task lifetimes

This patch wraps the registered manual account/proxy tasks, instance batch
tasks, and OCI instance-launch jobs with the existing SQLite-backed OCI
operation lease primitive. A worker registers a lease synchronously before
its first `await` and releases it on completion, error or cancellation.

Existing atomic task row admission remains the first line of defense:
the task row is created before a worker can be scheduled, and the durable
maintenance marker refuses to activate if there is a nonterminal task row.
Once running, the separate background lease protects the interval after
the task row may become terminal but while its worker is still finishing.

The durable maintenance marker's existing lease-drain check observes both
HTTP and background leases in the same SQLite transaction. All lease
registration failures abort the worker body. Crash-left leases require
manual investigation and are never cleared automatically.

## Not complete release authorization

- Notification tasks and non-registered background workers may still run.
- OCI SDK calls in external monitor/guard processes are not registered.
- A task cancelled before its first scheduled execution may leave a
  nonterminal task row; this is intentionally fail closed.
- A delayed OCI SDK thread can outlive a completed coroutine.
- This patch adds SQLite writes per background task. Review WAL, concurrent
  load and cancellation behavior before any production API deployment.
- A coordinated human-approved restart/migration/rollback flow is still
  unimplemented.

**No production rollout or backend container restart in this PR.**
