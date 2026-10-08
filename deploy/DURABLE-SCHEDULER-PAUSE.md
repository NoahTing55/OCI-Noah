# Durable release pause for scheduled account checks

The account-check scheduler now reads the durable SQLite release maintenance
marker before it loads or advances the next scheduled slot. When active,
the loop skips its iteration without consuming the pending run. This
complements HTTP admission protection and the repository-level task insert
transaction guard.

This is a narrow integration change. It is **not** a complete production
release/drain feature: in-flight OCI calls can persist, direct OCI routes have
a race window, and other background services must be audited. The release
marker is not activated by this change.

Before authorizing a backend restart, the full maintenance workflow must
hold durable admission, drain all running jobs, confirm no external OCI work
is in flight, create a verified SQLite backup, and receive operator approval.
Until then, no `--apply` or API restart should be introduced.
