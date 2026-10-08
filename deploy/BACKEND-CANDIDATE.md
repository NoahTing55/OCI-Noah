# Backend candidate API image preflight — read-only

The candidate checker is intentionally separate from deployment:

```bash
python3 deploy/backend-candidate-check.py --sha <full-40-character-commit-sha>
```

This does not pull, run, or restart an image. It requires the immutable
`ghcr.io/noahting55/oci-noah-api:sha-...` image to be **already present locally**.
It then uses only `docker image inspect` to confirm its local tag, image ID,
architecture and optional OCI revision label.

It does **not** validate remote registry provenance, the image's source code,
database migrations, API/schema compatibility or absence of active OCI work.
Its `release_authorized` and `candidate_compatibility_verified` are always
false, even when the tag and optional revision label match.

Completing deployment coordination still requires production release-mode
admission across processes, observed quiescence, a verified database snapshot
and an explicit operator approval. Do not restart API based on this check.
