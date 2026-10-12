# OCI-N&T 2.0 single-container RC: consolidation acceptance

## Fixed baseline and invariants

- Preserve the existing **2.0.0 Stable** front-end appearance, routes, tenant flows, proxy behavior and OCI controls; this is a deployment architecture change, not a UI rewrite.
- Production is `/opt/oci-nt`. **Never touch `/opt/oci--nt`**, its Compose project, resources or data.
- Schema 7 is the compatibility baseline. Encrypted OCI credentials and original SQLite data must remain usable; never replace them with sample data.
- Four existing services are the rollback baseline: API, Web, Monitor, Docker Guard. The single image supervises equivalent processes, but passing HTTP health checks alone does not prove feature parity.
- Mainline must stay usable during development. Pin images by full commit SHA/image ID, not mutable `latest`.
- No production operations from CI or this branch. No implicit production authorization when checklists are complete.

## Integrated RC acceptance matrix

| Gate | Evidence required | Current status |
|---|---|---|
| UI / routes / tenant and proxy parity | Compare live 2.0 stable behaviors with an isolated candidate, including authentication, cached reads, resource edits and failure paths | Not yet evidenced |
| All OCI mutation entry points | Enumerate route handlers, schedulers, detached tasks, external SDK calls and monitoring workers | Not yet independently evidenced |
| Maintenance and drain | Durable cross-process admission stops *new* OCI writes; independently observe all worker generations and active calls reach zero | Not yet independently evidenced |
| Scope isolation | Confirm identity of every owned container, mount, network and project; refuse `/opt/oci--nt` | CI safeguards exist; production proof absent |
| Four-service shutdown | Confirm all four stop, after maintenance/drain and before any DB snapshot | CI coordinator ordering exists; production proof absent |
| SQLite + credentials | Consistent stopped-DB backup, integrity and isolated restore of Schema 7 with encrypted credential readability | Synthetic CI backup exists; real sanitized restore absent |
| Candidate lifecycle | Single container starts all four roles, supervises failures, handles SIGTERM and retains data | Disposable CI integration exists; production proof absent |
| Rollback | Preserve failed candidate DB, validate old release readability, restart pinned original four and confirm function parity | Synthetic CI drill exists; original images+credentials proof absent |
| Docker Socket security | Review root supervisor, shared PID/network namespaces, guard UID and socket GID for explicit risk acceptance | Not approved |
| Operator approval | Explicit outage window, independent backup, no competing deployment, recovery owner and manual hold/reopen policy | Not approved |

## Safety and next implementation work

This RC branch introduces a **report-only** gate at `deploy/single/rc-readiness.py`, tested in CI. The evidence field names correspond to the matrix. All values reported true still return `cutover_apply_allowed=false`; JSON supplied by a caller is not independently trustworthy. The production executor, real quiescence collection and socket-boundary acceptance are **not yet implemented**.

Complete the inventory and observational proof in isolated, credential-free CI/sandbox testing before designing an operator-reviewed production adapter. Retain `release-controller.py --apply` and `cutover-transaction.py --apply` fail-closed until separately reviewed. Nothing in this document permits stopping production.
