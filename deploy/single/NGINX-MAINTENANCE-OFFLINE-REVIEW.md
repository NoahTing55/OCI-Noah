# OCI-N&T 2.0 — Nginx maintenance isolation review (53.3)

**PLAN ONLY / NO-GO.** Do not change live Nginx or reload it. No changes to `/opt/oci-nt` or `/opt/oci--nt`.

## Source-of-truth audit (2026-10-14)
- Main vhost: `/www/server/panel/vhost/nginx/oci.chenxierli.com.conf` (SHA256 `8ae12b5fccc46bd820c16eba77e51cab153aab8676315b79ed6683296f557573`).
- Proxy included file: `/www/server/panel/vhost/nginx/proxy/oci.chenxierli.com/e16d6563413e46a8b383f37957366e04_oci.chenxierli.com.conf` (SHA256 `b85f0895fd02f13860dbbcaf09f2a0c517c23f9f1fc4ffa843815b6689d36fc4`).
- Existing catchall is `location ^~ / { proxy_pass http://127.0.0.1:9859; ... }`.
- Main site also includes ACME well-known file, extensions, rewrite file, regex `location ~ \.well-known` and other locations.
- `proxy_read_timeout`/`proxy_send_timeout` = 3600s: freezing new requests does **not** drain active requests or OCI SDK calls.

## Important routing caveat
Nginx `location ^~ /` suppresses regex location selection for ordinary paths matched by that prefix. Thus an existing `location ~ \.well-known` is **NOT** sufficient proof that ACME challenge requests bypass the catchall. A more-specific prefix/exact location may win, depending on effective include tree and nested paths. **Do not assert ACME continuity until a real isolated Nginx test with the assembled config passes.** Do not add a duplicate `location /` in extensions.

## Offline test acceptance criteria
1. Operate only in a throwaway runner, isolated filesystem, synthetic HTTP upstream and synthetic ACME challenge. No Docker socket, real OCI data, production port or real domains required.
2. Assemble Nginx config with representative `location ^~ /` and `location ^~ /.well-known/acme-challenge/` serving synthetic challenge. Assert `nginx -t` success.
3. Baseline: `/` and `/api/v1/health` route to synthetic upstream; ACME challenge returns the synthetic token.
4. Maintenance: `/`, `/api/v1/health` and mutating paths return HTTP 503 with Retry-After and no-store; challenge continues to serve synthetic token.
5. Restore: hash baseline configuration, replace maintenance config with immutable baseline, test resumed proxy and token; compare SHA256.
6. All testing must be against the isolated runner config, **not** the live site config. The independent regression suite should fail if Nginx is unavailable; do not report a pass from a regex-only parser.
7. Treat **all other ingress** and in-flight SDK operations as separate fail-closed blockers.

## Production design constraints
- Prefer editing a precise copy of the single proxy-included file with verified hash and atomic restore; do not touch global Nginx or other vhosts. Fully resolve the actual ACME routing path before use.
- A future real change requires operator-approved maintenance window, fresh config snapshot, safe `nginx -t`, explicit scoped reload and health test; never reload automatically by an audit.
- Site-only HTTP 503 is not a complete OCI operation admission freeze. Independently block localhost API writers and background scheduler/workers; verify old-generation SDK drain before any stopped-generation snapshot.
- No production cutover or merge is authorized by this review.
