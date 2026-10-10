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
