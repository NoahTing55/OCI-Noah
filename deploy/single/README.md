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

## Integrated acceptance (CI only)

`deploy/single/ci-four-single-four.sh` is an **ephemeral GitHub-hosted runner
only** integration exercise. It compiles the four-container API/Web images,
boots the existing four-container Compose stack using synthetic credentials and
a new SQLite database, gracefully removes that stack, boots the single
candidate using the same disposable database, and finally stops the candidate
and reboots all four existing services. It checks real HTTP health and database
markers across both directions.

This does **not** constitute a production cutover rehearsal with live OCI
operations, genuine encrypted credentials, or an externally provided scrubbed
production snapshot. The existing production deployment and data are never
accessed. No production cutover mechanism is added. The root supervisor and
Docker Socket remain production security concerns; a true rollback after an
incompatible schema migration requires a tested data restore as well as old
images. The separate `readiness-audit.py` remains advisory and never
authorizes production release.

## Production inventory evidence (no cutover)

After the GitHub branch is reviewed and merged, a production operator may use
`python3 deploy/single/rollback-evidence.py --sha <40-hex-commit> --out /root/oci-nt-backups/single-release-evidence.json`
to produce an exclusive, mode-0600, **secret-free** inventory of the four
original immutable image tags. The output directory must already exist;
the command refuses to overwrite files and does not interact with other
stacks (especially `/opt/oci--nt`). It does **not** back up data or env files,
stop processes, assert detached OCI quiescence, or authorize a switch.

`release-controller.py --apply` remains deliberately unavailable. A later
production cutover would require proven admission across background and HTTP
OCI work, a pause/stop that cannot race detached processes, an isolated
DB+encrypted-credential restore test, Docker socket threat approval, and a
known-good four-service rollback using pinned images. Never treat an empty
lease registry as proof of all OCI activity being gone.
