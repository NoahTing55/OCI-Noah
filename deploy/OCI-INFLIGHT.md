# OCI HTTP in-flight tracking — instrumentation only

The middleware now counts admitted OCI HTTP mutation requests while their
request handlers run. Entries are removed in a `finally` block even on errors.
The tracker is in-memory and process-local. It intentionally exposes no
unauthenticated HTTP endpoint.

**NOT a release drain guarantee:** async tasks may continue after the response;
background schedulers, threadpool jobs, launch workers and other processes are
not counted. Middleware classification only covers routes identified in
`maintenance_route_guard.protected_oci_write`. There is no atomic handshake
between maintenance marker acquisition and already admitted HTTP requests.

A future release coordinator must aggregate **all** relevant workers and
in-flight operations, pause creation atomically, wait for completed tasks,
verify a consistent backup, then require operator approval. Never infer
restart safety from the local counter reading zero.

No production activation or backend deployment in this PR.
