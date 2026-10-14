"""All critical listeners must be healthy, not just API or Nginx."""
import sys
import os
import urllib.request

checks = [(9858, "/api/v1/health"), (9859, "/"), (9860, "/health")]
if os.environ.get("OCI_NT_EXTERNAL_GUARD", "0") != "1":
    checks.append((9861, "/health"))
for port, path in checks:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
    except Exception as exc:
        print(f"unhealthy {port}{path}: {exc}", file=sys.stderr)
        sys.exit(1)
sys.exit(0)
