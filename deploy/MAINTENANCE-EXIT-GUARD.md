# Durable maintenance exit safety barrier

Maintenance exit now uses the same SQLite `BEGIN IMMEDIATE` transaction
to ensure no outstanding OCI HTTP/background operation leases and no
nonterminal manual/launch task rows before reopening admission.

Malformed lease entries, unknown task statuses or missing task tables
fail closed; failure preserves the active durable maintenance marker.
This does not prove that all OCI SDK requests have ended across
uninstrumented processes, nor does it authorize backend deployment.
An eventual release coordinator must additionally verify external
worker quiescence and backup/rollback readiness.

This PR makes no production changes. Do not turn maintenance on or
restart API to exercise the exit path against production.
