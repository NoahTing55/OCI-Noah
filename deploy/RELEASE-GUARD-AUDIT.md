# OCI release-maintenance route audit

CI now checks a named inventory of critical OCI mutation endpoints against
`protected_oci_write`. This prevents silent loss of HTTP maintenance
coverage when an existing route is renamed or the guard's prefix policy changes.

The inventory includes account checks, task retries/resume, instance
sync/actions, public IP replacement, launch networking/jobs, proxy allocation
and scheduled checks. Cancellation, sign-in, backup and read-only diagnostics
are intentionally not blocked by the release guard.

This is **regression coverage**, not exhaustive source-to-sink security
analysis. Future OCI integrations and background operation paths require
manual audit. HTTP middleware can race with a gate transition, and task
database drain checks do not account for in-flight requests across API
workers. Do not enable backend deployment based on this test alone.

Before completing release coordination:
1. Enumerate OCI SDK call sites and classify every HTTP and background entry.
2. Track all active OCI operations in every process, not just route handlers.
3. Implement a shared admission/drain protocol with verifiable quiescence.
4. Verify an SQLite snapshot, version compatibility and operator approval.
