# Atomic admission: first repository integration

This PR adds an admission guard to the **existing** transaction of
`task_repository.create_task` and adds a `BEGIN IMMEDIATE` transaction in
`launch_repository.create_launch_job`. Both read the durable maintenance
marker while holding the same SQLite write lock used for task insertion.

When a valid marker has `active: true`, durable inserts into `manual_tasks`
and `launch_jobs` are blocked. Unknown/malformed marker content fails closed.
When no marker is present, existing task operations continue.

The guard does not create or toggle the maintenance marker.

## Remaining critical release blockers

- Task creation/activation and external OCI requests through other route paths
  (direct instance actions, sync, network ensure, etc.) need complete audit.
- Background schedulers, retries, resume and in-flight work require a shared
  drain protocol; simply checking a task table is insufficient.
- Cross-process monitor, guard and API responsibilities need validation.
- A safe operator-facing enter/exit maintenance workflow and recovery after
  interruption must be built and tested.
- Never use this PR alone to restart or replace a production backend image.

This is a source-level integration step. Backend deployment remains disabled.
