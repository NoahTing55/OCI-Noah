# OCI-N&T 2.0 — Production Cutover Gate (PLAN ONLY)

**Status: BLOCKED / NOT APPROVED.** This document is an operator review checklist, **not** an executable deployment or permission to stop live services. PR #66 stays Draft.

## Verified read-only production baseline (2026-10-14 08:19 UTC)
- Host: `spartan-dalkvm`; independent `/opt/oci--nt` and `oci--nt-app-1` must be left untouched.
- Live project `oci-nt`: `oci-nt-api`, `oci-nt-web`, `oci-nt-monitor-agent`, `oci-nt-docker-guard`, all healthy, Docker network `host`, ports bound only to 127.0.0.1:9858–9861.
- Legacy API/Monitor/Guard image ID: `sha256:d67ca61ac5129207e421d67834f5daa727aab1162541efee1f23dc1abb90bc27`; legacy Web: `sha256:21fbeac7176e41ba59773fba21887d05d10a6632f78bb61dd73e2d2f4d8002b4`. Local images confirmed present.
- Compose: `/opt/oci-nt/docker-compose.yml`; SHA256 `497417a52b8ea1a7304a3a510ff6dfb2600731e67953e101966f349c3d8b39dc`; inspect again immediately before any authorized change.
- External Nginx: `/www/server/panel/vhost/nginx/oci.chenxierli.com.conf`, with included proxy configuration forwarding `oci.chenxierli.com` to `127.0.0.1:9859`. Verify the *included* files and other ingress before writing a freeze procedure.
- Data: `/opt/oci-nt/data/oci-nt.db`, Schema 7, 26 OCI accounts as of audit, mode 0660 owned by UID/GID 10001. `manual_tasks`: COMPLETED 68, FAILED 3, PARTIAL 7 (all PROXY_HEALTH with finished_at), known active manual tasks 0; `launch_jobs` known active 0; account scheduler disabled.
- **No OCI lease tables** in the running old database. Nine threads in the old API are not proof of idle OCI SDK operations. A database status check alone **does not** establish quiescence.
- Verified tested RC image indexes: single `sha256:395e79d83495edd5f659b566d797ed160396a05ebb5ef098d62d853bf90da8bd`, external Guard `sha256:ecece550ded5e45f6db0acb3a7f71e629438de3f725d926da74618f1174ddbee`.
- GitHub-hosted runner CI #426 succeeded on synthetic Schema 7 four→two→four rollback. This does **not** establish production rollback safety.
- Historical backup from 2026-10-13 is **not** an acceptable cutover-point snapshot.

## Mandatory fail-closed gates (each requires new evidence)
1. Explicit operator authorization of the **maintenance window and exact change procedure**. Review alone never counts.
2. Confirm source-of-truth legacy Compose, tagged local images, mount ownership, and rollback executor. Preserve immutable copies and verify hashes. Keep independent projects outside all scopes.
3. Inventory all ingress, including Nginx include chain, localhost API, existing connections, task workers and any automation. Prepare a reversible OCI-site-only ingress freeze; test and document it before use.
4. During the **approved** window, prevent all new OCI operations, request admission from every entry path, and drain existing requests/workers. The running legacy version lacks global SDK quiescence attestation; a zero task count, lease count, or observed low CPU is **not sufficient**. If you cannot prove quiescence, **NO-GO**.
5. Verify **actual old generation process exit** only *after* approved drain: API, Monitor, Guard, including spawned workers and remote OCI operations. This is not proven by an absent listener alone.
6. With the old writer stopped, capture a **new consistent SQLite backup** (use SQLite backup API rather than copying a live WAL DB), verify SHA256, integrity, foreign keys, Schema 7, current account count, and independent restore. Record ownership and retention.
7. Verify exact new image indexes and guarded Compose mounts, unix socket ownership, port coexistence, database permissions, proxy/API credentials compatibility. Do not mount raw host Docker socket in the main workload.
8. Only after **separate explicit production deployment approval**, perform controlled mutually exclusive old/new service start and live functional validation. A CI-only dry-run is not authorization.
9. If any invariant fails: keep ingress frozen; stop the candidate, verify its processes have exited, decide whether restore is required for data consistency, restore approved legacy Compose/images and consistent DB as appropriate, validate old services and access before reopening ingress. **Never automatically overwrite live DB with historical data.**
10. Record final operator sign-off, hashes, health checks and incident timings. Do not merge Draft PR #66 or execute on production as part of this checklist.

## Fail-closed evidence tools already in the repository
- `deploy/single/cutover-readiness-report.py`: read-only observation only; always reports no authorization.
- `deploy/single/stopped-generation-evidence.py`: can document process exit, not SDK drain.
- `deploy/single/cutover-snapshot-gate.py`: checks offline immutable snapshot hash/schema/count, **not** capture-time consistency.
- `deploy/single/final-rollback-admission.py`: plan-only rollback evidence; **not** live approval.
- `deploy/single/controlled-cutover-sequence.py`: order only; no production commands.

## Explicit blockers as of this document
- Production ingress is **not frozen**; OCI SDK global quiescence **not proven**.
- No approved production change window or production executor.
- No verified **cutover-time** snapshot, and no live four↔two rollback drill.
- The review-only Compose uses host networking and production ports: **NEVER run it on a host while the old generation is listening**.
