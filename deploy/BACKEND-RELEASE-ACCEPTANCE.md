# OCI-N&T backend release — final acceptance gate

This is a consolidated release review. A checked task database is **not**
an authorization to restart the API, upgrade the OCI-N&T schema or recreate
monitor/guard services.

## Hard blockers before production deployment

1. **Independent verification of all live OCI entry points.** HTTP request
   and background-task leases apply only where instrumented. Audit all
   direct OCI SDK callers, especially any detached thread and scheduler,
   and prove no SDK call survives the lifetime of its lease.
2. **Release-maintenance orchestration.** An authenticated/operator-driven
   coordinator must atomically stop new OCI work, await already active work
   without terminating it, and verify both durable leases and task rows.
   The current low-level marker function is not that coordinator.
3. **Candidate migration safety.** Run an isolated image/schema-compatibility
   test against a *copy* of the production SQLite database. The API lifespan
   calls `init_database()` before serving health and could perform mutations
   on startup. Do not launch a candidate API on the live data directory.
4. **Rollback.** Verify a WAL-safe release snapshot and its SHA-256, check
   restore drill against a disposable directory, and prove that the old API
   image and old DB schema can be restored together. A code-only rollback
   might not undo migrations.
5. **Explicit narrow execution.** Pin API and monitor/guard image separately
   from web; never use an unrestricted `docker compose up -d` with shared
   `OCI_NOAH_IMAGE_TAG`. Require an operator-confirmed SHA and preflight
   evidence. Verify health of every service and preserve old image digests.
6. **Real CI + staging.** Tests must pass; dry-run the coordinator on a
   disposable environment and test cancellation, admission racing, crashed
   workers, corrupted leases, process termination and rollback.

## Decision

**Backend API production upgrade is BLOCKED** until all conditions are
implemented and validated. The GitHub modules are not deployed in the old
running API image just because their PRs were merged.

`deploy/backend-preflight.py` now returns
`db_task_rows_terminal` independently of `safe_to_restart: false` and
`release_authorized: false`; its zero exit status means only the former.

The release tooling here does not start maintenance, mutate production
SQLite, pull candidates or restart any service.
