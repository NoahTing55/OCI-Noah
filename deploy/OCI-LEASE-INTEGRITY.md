# Persistent OCI lease integrity hardening

Existing request-admission code checked that the lease registry was a JSON
object, but could accidentally accept malformed entry values and write the
registry back when registering a new request. PR #32 rejects corrupted
records before acquiring, releasing, or counting leases.

Entries must contain a nonempty token and nonempty string `started_at`
and `path` fields. Unexpected records remain intact for manual
investigation; no automatic migration or deletion takes place.

This does not verify that a remote OCI request has terminated. Existing
cross-process gaps and the restriction against production backend upgrade
remain unchanged. CI now exercises the fail-closed behavior.
