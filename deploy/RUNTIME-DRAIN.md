# In-process runtime drain snapshot — diagnostic building block

`backend/app/runtime_drain_snapshot.py` aggregates currently running
account/proxy, instance-batch and launch asyncio task registries, launch
notifications, and the current-process OCI HTTP write counter.

It deliberately has no new HTTP endpoint or operator toggle, and is not
connected to a deployment script. Its results are current-process snapshots
only: another API worker, independent monitor/guard processes, queued
untracked threads and detached OCI SDK calls may still be running.

The field `release_authorized` is **always false**.

To use this for a safe release, first integrate persistent admission,
all-process idle verification, task lifecycle audit, maintenance-mode
control and verified backup with strict manual approval. Do not restart
production based only on a zero count.
