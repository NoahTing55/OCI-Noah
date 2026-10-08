# HTTP OCI mutation guard — release maintenance

Adds a FastAPI middleware check against the persistent SQLite release
maintenance marker before allowing HTTP OCI mutation routes (accounts,
instances, bulk, tasks, launch, proxy operations, and scheduled check
settings). GET requests, authentication, health and backup administration
are unaffected. Cancellation routes remain usable for draining.

On active/malformed/unreadable maintenance state, protected OCI writes return
HTTP 503. No operator toggle is included in this PR; the marker is not
automatically activated. Unit tests verify routing classification and marker
behavior.

## Critical remaining gaps

- This middleware check is **not an atomic gate** for direct OCI API calls.
  Requests may pass the check just before the marker becomes active.
- Work already in flight, periodic schedules and non-HTTP worker activity
  require a coordinated drain protocol, with separate start-prevention checks.
- Coverage must be audited against all direct OCI functions and future API
  routes; route matching is intentionally conservative.
- The repository's existing SQLite transaction guards protect only durable
  manual_tasks and launch_jobs inserts, not every direct OCI call.
- Must add authenticated maintenance entry/exit, safe draining and a verified
  backup before attempting deployment.

**Do not deploy a production API based solely on this guard.**
