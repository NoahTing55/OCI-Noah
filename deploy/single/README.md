# Experimental single-container OCI-N&T

This is a **lab candidate only** and is NOT a production upgrade procedure.

- Runs nginx, API, monitor agent, and Docker guard as four supervised child
  processes within **one** container. Python workers run as UID 10001; nginx
  master starts as root to bind/start Nginx. A critical child exit terminates
  the container; Docker restart policy restarts all components.
- The existing production docker-compose.yml remains **unchanged**.
- This stack uses the same localhost ports (9858/9859/9860/9861). **Never run
  concurrently with the four-container deployment on the production host.**
- Lab experiments must use synthetic credentials or a read-only/sanitized
  snapshot, not live production data paths. Docker guard access to the Docker
  socket grants significant authority and must be threat-modeled.
- Test process restarts, memory, secrets ownership, SQLite WAL, monitoring,
  graceful termination, OCI in-flight admission/drain, ingress and rollback.
  Host networking and the Docker socket make a generic untrusted test host
  unsuitable.
- No automatic migration, no replacement Compose file, no production switch
  commands are provided. Verify a disposable deployment before planning cutover.

## Release safety boundary

The `offline-cutover-drill.py` utility is **only a source-preserving,
disposable data copy rehearsal**. It checks both candidate and rollback
SQLite copies against a verified input snapshot. Its success explicitly does
not establish that the current four-container images can read any database
that a new API has subsequently migrated, or that containers can actually be
rolled back. Do not invoke it on live files or treat it as permission to cut
over.

Outstanding production gates (not covered by this PR):
- Capture real OCI operation leases / in-flight HTTP calls and enforce
  maintenance admission with an atomic stop-and-drain protocol.
- Validate a sanitized snapshot from the actual environment in an isolated
  sandbox with no OCI egress, no production credentials and no live bind mounts.
- Test image-level switch and reversal, including DB schema forward/backward
  compatibility; an image rollback alone cannot undo migrations.
- Review the all-in-one threat model. The Docker socket is a privileged host
  control interface even mounted `:ro`; a process sharing the same container
  may be able to reach it. The current all-in-one design retains elevated
  security risk versus isolated services.
- Prove resource use, filesystem ownership and security of Nginx master,
  workers and the API's credential handling.

The normal four-container production Compose remains authoritative until
these gates are satisfied and a separately reviewed cutover procedure exists.

## Experimental socket group isolation

The guard now runs under UID 10002, with the explicit host Docker socket GID; API and monitor run under UID 10001 with no supplemental groups. Supply the non-root `DOCKER_GID` in an isolated lab. This is only a **partial risk reduction**, not a Docker Socket sandbox: the Nginx/supervisor root process and shared namespaces remain powerful and prevent declaring single-container production security parity with the four-container topology. Avoid use on the production host.
