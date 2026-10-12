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

## CI evidence versus production release blockers

A green GitHub Actions run proves only that the **disposable fixtures and
contract tests** passed on the corresponding commit. It is not production OCI
quiescence, a credential restore attestation, or acceptance of Docker socket
risk. The readiness and preflight programs are **report-only** and their
`--apply` paths remain blocked.

| Evidence family | Disposable CI coverage | Still required before a production change |
|---|---|---|
| UI and OpenAPI | HTTP asset hashes and route/component comparisons across four → single → four | Representative authenticated real tenant/proxy flows and failure handling |
| SQLite and credentials | Schema 7, stopped backup integrity, synthetic Fernet secret, four-container rollback | Offline backup and isolated restore using securely handled real-compatible data and retained key; no sensitive logs |
| OCI task admission | Atomic SQLite maintenance/lease races, cancelled workers and spawn-site inventory | Independent inventory and observation for all actual OCI SDK egress, scheduler generations and detached threads |
| Single-container lifecycle | Real Docker process UID/group tests, fail-fast, restart and disposable persistent volume | Reviewed host-socket privilege boundary and restart/health policy on the actual host |
| Rollback images | Read-only pinned-image inventory contracts | Confirm exact running image IDs, old four-image availability and old release boot compatibility |
| Environment scope | Ownership and foreign-project rejection contracts | Confirm mounts/networks/project identity for `/opt/oci-nt`; never alter `/opt/oci--nt` |

### Explicit operator hold points

Before any proposed production change, an operator must independently approve
the outage window, record four immutable original image IDs, review the Docker
socket's host-level privilege implications, and verify no other deployment is
running. Do not turn a read-only inspection into a production stop command.

A safe rollback design must retain the pre-cutover SQLite snapshot and encryption
key (without exposing the key in logs), preserve the failed candidate database for
forensics, test Schema 7 and credential readability with the original images in
an **isolated** environment, and avoid overwriting newer production writes.

During maintenance, an empty current-process Worker snapshot or HTTP health check
does not prove that all OCI operations drained. Unknown or crash-left leases,
untracked schedulers, and missing cross-process generation evidence all require a
manual hold. Restoring normal admission must be a separate deliberate decision.

**Release decision:** `NOT AUTHORIZED`. PR #66 must remain Draft until an
independently reviewed production executor, live quiescence attestation and
explicit operator acceptance exist. No steps here authorize deployment.

## Safety and next implementation work

This RC branch introduces a **report-only** gate at `deploy/single/rc-readiness.py`, tested in CI. The evidence field names correspond to the matrix. All values reported true still return `cutover_apply_allowed=false`; JSON supplied by a caller is not independently trustworthy. The production executor, real quiescence collection and socket-boundary acceptance are **not yet implemented**.

Complete the inventory and observational proof in isolated, credential-free CI/sandbox testing before designing an operator-reviewed production adapter. Retain `release-controller.py --apply` and `cutover-transaction.py --apply` fail-closed until separately reviewed. Nothing in this document permits stopping production.
