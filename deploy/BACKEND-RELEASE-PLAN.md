# Backend release coordinator: plan only

This entry point combines currently available backend inspection tools into
an ordered, explicit release checklist. It is intentionally a **non-executable
plan**; it cannot enable maintenance, make a backup, migrate, restart containers,
or approve a deploy.

Example (only when an immutable candidate API image has been staged locally):

```bash
python3 deploy/backend-release-plan.py --db data/oci-nt.db --sha <40-char-Git-SHA>
```

Even when every observed checker passes, `release_authorized` and
`execution_supported` remain `false`.

The plan makes remaining critical engineering work concrete:
- Coordinated, authenticated and durable task admission barrier
- True cross-process draining of OCI SDK requests and background tasks
- Verified WAL-safe backup **after** admission is frozen and drained
- Candidate image schema compatibility and tested rollback
- Explicit human approval, independent service pinning and audit

**Do not treat this as a production safe-upgrade command.**
