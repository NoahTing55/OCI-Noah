# Authenticated release runtime diagnostic (observability only)

Adds `GET /api/v1/system/release/runtime-drain` to the main API. It
requires the same logged-in user authentication as other system endpoints.
The response aggregates only the active API process' known asyncio task
registries and HTTP OCI write counter.

This endpoint is **read-only**. It does not enter maintenance, stop jobs,
restart containers, or authorize a release; `release_authorized` is always
false. The endpoint is not a cross-process task drain guarantee: other
workers, OCI calls that outlive request handling, untracked threads and
other processes may still be active.

Do not call it a safe-to-upgrade flag. Production backend deployment remains
blocked pending all-process quiescence and approved release procedures.
