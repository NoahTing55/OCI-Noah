# Atomic OCI HTTP admission leases — review before backend deployment

This change narrows a real maintenance race in protected OCI HTTP writes:
the API now atomically checks the durable maintenance marker and registers a
request lease in one SQLite `BEGIN IMMEDIATE` transaction before allowing
the handler to start. On completion, it removes that lease in a separate
transaction. The durable maintenance marker's `begin` operation now refuses
activation if any outstanding leases exist, under the same SQLite write lock.

The active leases live in `system_settings` under
`oci_nt_http_operation_leases_v1`. Unexpected worker exits intentionally
leave leases behind, blocking maintenance until the operator investigates
the failed operation. Never automatically clear them based on age.

## Remaining safety gaps

This does **not** safely authorize a backend release:
- Only protected HTTP routes get a durable lease. The existing account,
  launch and instance background tasks do not have an equivalent durable
  lifetime lease yet.
- Request handlers that schedule detached work may release the HTTP lease
  before the actual OCI operation finishes.
- The existing `durable_maintenance.begin` is a lower-level building
  block, not an authenticated operator release coordinator.
- State serialization adds SQLite writes for protected HTTP operations.
  Validate latency and contention before production API deployment.
- Migration/schema compatibility, crash recovery and rollback need testing.
- The middleware must reject operations on lease registration failure; any
  lease release failure intentionally requires investigation.

No maintenance activation, production restart, database migration or
automatic backend deployment is performed by merging this PR.
