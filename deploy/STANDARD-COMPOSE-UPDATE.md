# GitHub GHCR build + standard Docker Compose image updates

GitHub Actions on main compiles/tests before publishing two immutable images:
`ghcr.io/noahting55/oci-noah-api:sha-<40-char-main-sha>`
and `ghcr.io/noahting55/oci-noah-web:sha-<40-char-main-sha>`.
A green PR build alone does not publish these images.

The base Compose file uses **independent** tags:

- `OCI_NOAH_BACKEND_TAG` for API, Monitor Agent, Docker Guard;
- `OCI_NOAH_WEB_TAG` for Web.

The built-in defaults are pinned to versions found running on 2026-10-08.
The old single `OCI_NOAH_IMAGE_TAG` no longer drives either service.
Neither backend nor Web automatically jumps to `latest` after a git pull.

After merge and successful main push image builds, a future operator can
intentionally set the target tags in /opt/oci-nt/.env. **First examine the
resolved image names** with `docker compose config --images` and review
the effects. The familiar command

```bash
docker compose pull && docker compose up -d
```

downloads images and may recreate services (including Web, API, monitor,
guard); `up -d` is NOT an OCI task drain, does not guarantee zero
in-flight OCI requests, and cannot safely roll back DB mutations.
**Do not run the command to switch production API from the old revision
until cross-process work drain is independently demonstrated and a
maintenance window with explicit authorization is in place.**

The deployment release preflight remains conservative by design.
For Web-only changes use the pre-existing `deploy/release-web.sh`
procedure with explicit SHA and `--apply`, not an unrestricted
whole-stack Compose update.
