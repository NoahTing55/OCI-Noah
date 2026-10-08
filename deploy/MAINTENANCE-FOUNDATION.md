# Maintenance admission foundation — not yet production release-safe

This change reuses the existing **in-process** `maintenance_state` signal
(already used for database maintenance) to reject task starts from:
- manual and scheduled account checking;
- proxy health checking;
- instance batch operations;
- launch task creation.

The account-check scheduler also skips its polling iteration while the
in-process maintenance flag is set. Existing work is not force-cancelled.

## Important limitations

**Do not use this foundation to authorize a backend restart.** The current
maintenance flag is process-local and is cleared when the API process
restarts. The task guard is a check, not an atomic admission/lock transaction:
another task can race with entering maintenance. Other task initiation paths
and direct OCI actions still require a complete audit.

Before enabling backend deployment:
1. Implement a durable maintenance gate shared by all relevant processes.
2. Ensure a transactionally enforced barrier or draining protocol preventing
   new task admission after maintenance begins.
3. Cover each automatic scheduler, manual/retry/resume and direct OCI mutation
   path, including pending work already scheduled.
4. Verify zero in-flight operations while the gate is held, then create a
   consistent verified release backup.
5. Add restart-time behavior, tests with concurrent task submissions and
   explicit authenticated authorization to enter/exit maintenance.

This PR has no frontend control and does not automatically enter maintenance
mode. Merging it must not trigger a production backend deployment.
