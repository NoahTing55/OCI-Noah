# OCI lease recovery diagnostics

The read-only `deploy/oci-lease-diagnostics.py` reports persistent OCI
HTTP and background operation lease counts from SQLite. It never deletes,
expires or releases a lease and never exposes the opaque lease tokens.

Example:

```bash
python3 deploy/oci-lease-diagnostics.py --db /opt/oci-nt/data/oci-nt.db
```

An outstanding lease after a crash can be a sign of interrupted execution,
not definitive evidence of a currently active OCI SDK request. **Do not
automatically clear it based on time or the process no longer existing.**
Investigate the underlying task and OCI remote resource state first.

Zero recorded leases also does not prove that all work is drained: old
production images, unregistered workers and detached SDK requests may
not record leases. `release_authorized` remains false in all responses.

The tool reads the database using `mode=ro` and `query_only`; it does
not enable maintenance, modify tasks or restart any service.
