# Backend release evidence collector (read-only)

Once merged, the operator may run:

```bash
cd /opt/oci-nt
python3 deploy/backend-release-evidence.py --db data/oci-nt.db
```

This tool performs no database writes and does not invoke `docker compose`,
`docker restart`, or `docker pull`. It collects the durable maintenance flag
and nonterminal task counts through `release-drain-check.py`, then inspects
the current states of API, Web, Monitor and Docker Guard containers.

**Warning:** This does NOT authorize a backend deployment, even when
`evidence_complete` is true. The result is always
`release_authorized: false`. It cannot see all in-flight OCI calls,
detached background work or competing API workers. In addition it makes
no assertion that the candidate API image is compatible with the current
database or that recovery can succeed.

The report will normally be BLOCKED in everyday operations because the
persistent maintenance flag is inactive. Never enable maintenance merely
to make this diagnostic turn green. A fully reviewed and authenticated
release coordinator must acquire the gate, drain all work, validate an
independent SQLite backup and require explicit operator authorization.
