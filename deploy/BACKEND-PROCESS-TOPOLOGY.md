# Backend process topology audit (read-only)

This standalone utility reads `docker top` process counts from the API,
monitor-agent and docker-guard containers. It never prints command-line
arguments (which may reveal secrets).

```bash
cd /opt/oci-nt
python3 deploy/backend-process-topology.py
```

Even if every process list is readable, **this cannot establish safe
quiescence**. Process counts cannot detect active threads, remote OCI
requests, queued jobs or races against new task admission.
`release_authorized` and `cross_process_quiescence_verified` always remain
false.

This is a topology inventory for designing the eventual cross-process
drain protocol, not an upgrade gate. No production containers are changed.
