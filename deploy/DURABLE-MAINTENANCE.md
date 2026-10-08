# Durable maintenance marker foundation

This module adds a reusable SQLite-backed state primitive in
`deploy/durable_maintenance.py`. It is NOT wired to task creators, schedulers,
the API middleware, or deployment scripts. It has no executable CLI interface;
importing it does not enter maintenance.

The module uses SQLite transactions for an exclusive begin/end marker and
deliberately fails closed when state is malformed. Unlike the existing
in-process state, its marker survives API restarts.

**This is not yet a safe maintenance mode.** There is no transactionally
coordinated admission gate for manual and scheduled jobs, no all-entry-point
coverage, no verification of in-flight OCI calls, and no authenticated control
surface for operators. No backend deployment or restart must rely on this
marker alone.

Implementation sequence:
1. Build a shared atomic task-admission protocol coordinated with the marker;
2. Audit and block every relevant job, scheduler and direct OCI operation;
3. Pause new work, drain existing work, and verify drained state;
4. Generate a verified release backup after gate acquisition;
5. Create a separately approved deployment/rollback transaction.

The added tests cover persistence, double acquisition, illegal release,
malformed marker handling, and missing database behavior.
