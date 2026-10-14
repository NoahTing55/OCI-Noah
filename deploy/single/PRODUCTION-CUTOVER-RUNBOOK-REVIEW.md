# OCI-N&T 2.0 production cutover runbook — REVIEW ONLY / NO-GO

This document describes a **proposed** state machine, not a shell runbook. **No command here authorizes changes.** Keep PR #66 Draft. Do not run the two-service review Compose on production: host networking collides with the existing 9858–9860 listeners. Keep `/opt/oci--nt` and `oci--nt-app-1` outside scope.

## Audited facts (2026-10-14 09:01 UTC)

- Legacy deployment: Compose `/opt/oci-nt/docker-compose.yml`, SHA256 `497417a52b8ea1a7304a3a510ff6dfb2600731e67953e101966f349c3d8b39dc`; all four old services running and healthy. Image IDs are fixed in `PRODUCTION-CUTOVER-GATES.md`.
- Host Nginx site file `/www/server/panel/vhost/nginx/oci.chenxierli.com.conf`; included proxy forwards to `127.0.0.1:9859`. Other includes, clients and local API writers have **not** been exhaustively inventoried.
- DB: schema 7 / 26 accounts / 0 known nonterminal manual and launch jobs / scheduler disabled. The seven `PARTIAL` proxy health tasks all have `finished_at`. This is **not** global SDK quiescence evidence.
- Existing historical backup has matching hash and 26 accounts, but is **not** a cutover-time backup.
- CI #426 completed synthetic four→two→four rollback. CI #429 passed source tests. Production rollback remains unproven.
- Read-only audit `deploy/single/production-readonly-audit.py` reports `production_cutover_authorized:false`.

## Proposed operator-approved state transitions (not executable)

| State | Required evidence / human decision | Failure response |
| --- | --- | --- |
| A. PREPARE | Explicit scope, maintenance window and distinct authorization for live operations; validated old Compose and pinned images; approved fresh backup and rollback method; confirmed disk/storage & owners | NO-GO. Do not touch production |
| B. FREEZE | Review Nginx include chain; verify reversible site-only response policy; deny new API mutations **including localhost and other admission paths**; observe active sockets and queued workers | NO-GO if any ingress remains unaccounted for |
| C. DRAIN | Observe completion/cancellation semantics of all in-flight OCI SDK calls; no new requests; don't equate DB statuses, lease=0, sleeping thread or health=200 to proof | NO-GO if SDK drain is not positively attested |
| D. OLD_EXIT | Only under separate authorization, orderly stop of old API/monitor/guard **and Web**, verifying *all four* exited and no old generation processes remain; release 9858–9861 | Abort and restore original generation; don't start candidate |
| E. SNAPSHOT | With old writer stopped, create consistent SQLite backup, preserving WAL state through SQLite backup API; independent restore, SHA256, integrity, schema=7, count=live, encrypted key verification without outputting plaintext | Abort; restore old four, no new generation |
| F. CANDIDATE | Verify pinned main/Guard image digests, Unix socket permissions and guard Docker API ACL; main must not access raw Docker socket; start only after old processes/ports absent and separate production approval | Candidate stop first; verify exit/ports, then rollback |
| G. ACCEPT | Check local API and Web proxy, login, read-only tenant/instance views, monitor/Guard, process health and data continuity; verify expected no OCI launch/check calls on initial smoke tests | Roll back if any mismatch |
| H. OPEN | Human acceptance; restore intended Nginx routes and any paused admissions; monitor health/errors and document hashes, times, owner | Keep site frozen until old or new path verified |
| R. ROLLBACK | Candidate stopped/old ports free; classify **whether candidate wrote/migrated DB**; restore pre-cutover DB *only if needed after explicit approval* to avoid losing newly committed data; original pinned Compose/images; verify old health before ingress reopening | Keep freeze; escalate; no automatic destructive DB overwrite |

## Separate approval checkpoints
1. Approval of the final **live** executor and maintenance window (not given).
2. Approval to freeze live ingress and stop old-generation processes (not given).
3. Approval to start live two-service candidate and, if needed, approval for database restoration (not given).
4. Acceptance to reopen external access (not given).

## Non-negotiable invariants
- No simultaneous old and new bind to production ports. No `docker compose down -v`, pruning or uncontrolled `docker system prune`.
- Do not change or query credentials unnecessarily; never print decrypted OCI key material.
- Do not copy a live SQLite main DB file without handling WAL safely; snapshot must be consistent and restoration tested.
- Bind-mount `:ro` on Docker socket is not an authorization boundary by itself; application Guard whitelist must be verified.
- A process-exit record proves only that host processes exited, **not** completed remote OCI operations; requests may have already been accepted remotely.
- STOP/NO-GO at any unverified gate. This file must not be converted to an unattended production migration without separate design and user approval.
