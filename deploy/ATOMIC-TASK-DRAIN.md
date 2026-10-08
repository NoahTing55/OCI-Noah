# Atomic task-drain check before durable maintenance activation

The durable maintenance marker now refuses activation if any rows in
`manual_tasks` or `launch_jobs` remain nonterminal. This check and the
existing OCI HTTP lease check take place **inside the same SQLite
BEGIN IMMEDIATE transaction** that activates the marker. Task repository
creators also hold BEGIN IMMEDIATE, so task insertions cannot race past
maintenance entry.

Unknown and NULL task statuses fail closed. Missing task tables or status
columns also fail closed. Previous marker and lease test fixtures are
extended to include the required task tables.

## Crucial limit

This does **not** prove all remote OCI work is complete. A row may have
reached a terminal state while detached tasks, SDK threads, callbacks,
or other process work remains. Direct OCI HTTP routes are covered only
where the operation lease middleware applies. The operator maintenance
entry/exit workflow is not yet integrated or approved.

**Do not use this as authorization for production API restart or upgrade.**
