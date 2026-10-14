# Single-container Docker socket security boundary (RC blocker)

Status: **unresolved, fail closed**. This document is an architectural finding, not a production deployment authorization.

## Observed design

- `supervise.py` starts as root and spawns API and monitor as UID 10001; Docker Guard as UID 10002 with the host Docker socket GID.
- Nginx is spawned without an explicit identity drop in `supervise.py`.
- The experimental single-container Compose file bind-mounts the raw host `/var/run/docker.sock` into the same container as the root supervisor.
- `docker.sock:ro` only makes the socket filesystem entry read-only. It **does not** make Docker API requests read-only.
- The HTTP guard GET allowlist limits traffic that actually traverses the guard, but **cannot** constrain a root process that connects directly to the mounted Unix socket.
- Successful CI child UID/GID tests prove child separation only; they do not prove host Docker daemon isolation.

## Required safe architecture

The production single-container workload **must not receive the raw host Docker socket**. Place the narrowly filtered Docker read-only API behind an independently isolated boundary (e.g. an external rootless proxy or separately sandboxed service) and have the monitor consume that API. Secure the proxy endpoint with an appropriate isolation/authentication model; binding an unauthenticated proxy to host TCP is not acceptable. If a second runtime component is unacceptable, retain the current four-container production deployment until another reviewed architecture exists.

The following requirements must be demonstrated in disposable CI:

1. The proposed production Compose mounts **no** host Docker socket in the all-in-one container and adds **no** supplemental host Docker socket group.
2. The all-in-one container's root Supervisor, Nginx, API, Monitor, and Guard cannot establish a direct connection to the host Docker socket.
3. Any approved monitor transport exposes the exact read-only endpoint allowlist, blocks mutations and ambiguous paths, is accessible only to intended clients, and requires no Docker credentials in application containers.
4. Verify actual process UID/GID/Groups, process cleanup, service health, restart and rollback behavior in disposable Docker environments.
5. Complete independent operator review of the Docker API threat model and real production OCI quiescence.

**Do not** reuse `deploy/compose.single.yml` for production: it is an experimental test scaffold with a raw host Docker socket mount. The current candidate digest `sha256:f9d1ed61ef3fd676b9d0023591e46427e2f80e1ba869512a4642c9c7e3618ca3` is an offline schema/credentials compatibility artifact, not an approved deployment image.

`rc-readiness.py` requires `root_supervisor_socket_boundary_resolved` and still rejects production `--apply` regardless of self-reported evidence. Production remains on its current four-container Compose configuration.


## Experimental external-Guard implementation (not yet approved)

The branch now includes `deploy/compose.external-guard-lab.yml` and
`deploy/single/Dockerfile.guard`. This is a **two-container laboratory
topology**: the Guard has the raw socket and no network, while the
all-in-one workload mounts only the filtered Unix socket read-only.
`OCI_NT_EXTERNAL_GUARD=1` prevents the workload supervisor from launching
an internal Guard, and the monitor prefers `DOCKER_GUARD_UNIX_SOCKET`.
The legacy `deploy/compose.single.yml` remains **unsafe for production**
and is retained only for historical isolated CI comparisons.

The new source-contract and fake-Docker Unix-transport tests are additive.
Before the experimental topology can be considered for production, CI must
also exercise two real disposable containers together (including socket
volume initialization, Guard recovery, missing/unavailable Guard behavior,
Nginx and API health, and complete rollback). Approval also requires
independent threat review and production OCI quiescence evidence.

**Do not run either lab Compose file against the active production host.**
