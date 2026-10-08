# Unified backend release evidence preflight

The read-only `deploy/backend-release-preflight.py` consolidates four
existing checkers into one report: current SQLite task state, durable-gated
database drain, current Docker service health, and locally staged API image
metadata.

Example (only after the candidate image has been staged separately):

```bash
cd /opt/oci-nt
python3 deploy/backend-release-preflight.py \
  --db /opt/oci-nt/data/oci-nt.db \
  --sha <40-character-commit-sha>
```

**Never interpret success as production release approval.**
`release_authorized` is always false. Even `EVIDENCE_ONLY` does not
prove all API processes and OCI SDK background calls are idle; it does not
verify candidate database schema/migration compatibility and it does not
confirm a rollback can restore prior state. A normal operation without a
durable maintenance lock will report BLOCKED.

The script only runs fixed local read-only inspection programs. It does
not enter maintenance, pull or execute images, restart containers, or
migrate a database. Human approval and a coordinated task admission/drain
protocol must be developed and separately validated before introducing
any backend deployment apply mode.
