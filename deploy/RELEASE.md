# OCI-N&T safe releases (phase 1: Web)

Production deployment requires explicit human approval. The CI/GHCR workflow continues to build images from `main` after tests pass. **No GitHub runner receives production SSH access or database secrets.**

## Web-only release

On the production host after the PR is merged and the GHCR `Publish web image` job succeeds:

```bash
cd /opt/oci-nt
git fetch origin
git pull --ff-only origin main
# Replace this with the 40-character merge commit SHA:
SHA=<40-character-merge-sha>
bash deploy/release-web.sh "$SHA"          # read-only preflight
bash deploy/release-web.sh "$SHA" --apply  # approved deployment
```

The override file pins **only** the Web service image and the deploy action always uses `--no-deps --no-build --force-recreate web`. The API, monitor and Docker Guard keep their current running images. The script checks running image, health, asset files, HTTP response, and automatically attempts Web rollback on a failed post-deploy check. Deploy state is saved under `/root/oci-nt-backups/web-release-...` with root-only permissions; do not share the JSON dump publicly.

## Manual rollback

Read `previous-image.txt` from the deployment backup, then:

```bash
cd /opt/oci-nt
export OCI_NT_RELEASE_WEB_IMAGE="$(cat /root/oci-nt-backups/web-release-YYYYMMDDTHHMMSSZ/previous-image.txt)"
docker compose -f docker-compose.yml -f deploy/compose.web-release.yml config --format json | python3 -c 'import json,sys;print(json.load(sys.stdin)["services"]["web"]["image"])'
docker compose -f docker-compose.yml -f deploy/compose.web-release.yml up -d --no-deps --no-build --force-recreate web
```

## Next phases

1. Separate immutable API/worker deployment, API compatibility checks, backend test coverage, and backup/restore rehearsals.
2. Production-safe deployment receipt and release history, with idempotency and audit trail.
3. Optional protected GitHub Environment and approval gate for a production workflow; **not enabled** by this phase.

Do not use global `OCI_NOAH_IMAGE_TAG` for a Web-only upgrade: it is shared by Web, API, monitor and guard. Do not run `docker compose up -d` for all services when only Web is changing.
