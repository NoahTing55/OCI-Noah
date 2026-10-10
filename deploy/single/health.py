"""All critical listeners must be healthy, not just API or Nginx."""
import sys
import urllib.request

for port, path in [(9858, "/api/v1/health"), (9859, "/"), (9860, "/health"), (9861, "/health")]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
    except Exception as exc:
        print(f"unhealthy {port}{path}: {exc}", file=sys.stderr)
        sys.exit(1)
sys.exit(0)
