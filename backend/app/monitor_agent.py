from __future__ import annotations

import http.client
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SOCKET_PATH = os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
DOCKER_GUARD_HOST = os.getenv("DOCKER_GUARD_HOST", "").strip()
DOCKER_GUARD_PORT = int(os.getenv("DOCKER_GUARD_PORT", "9861"))
LISTEN_HOST = os.getenv("MONITOR_AGENT_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("MONITOR_AGENT_PORT", "9860"))
DEFAULT_NAMES = {"oci-nt-api", "oci-nt-web", "oci-nt-monitor-agent", "oci-nt-docker-guard"}


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 3.0):
        super().__init__("localhost", timeout=timeout)
        self.unix_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.unix_path)
        self.sock = sock


def docker_get(path: str) -> dict | list:
    connection = (
        http.client.HTTPConnection(
            DOCKER_GUARD_HOST,
            DOCKER_GUARD_PORT,
            timeout=3.0,
        )
        if DOCKER_GUARD_HOST
        else UnixHTTPConnection(SOCKET_PATH)
    )
    try:
        connection.request("GET", path, headers={"Host": "localhost"})
        response = connection.getresponse()
        body = response.read()
        if response.status >= 400:
            raise RuntimeError(f"Docker API {response.status}: {body[:300]!r}")
        return json.loads(body.decode("utf-8")) if body else {}
    finally:
        connection.close()


def _cpu_percent(stats: dict) -> float:
    cpu = stats.get("cpu_stats") or {}
    pre = stats.get("precpu_stats") or {}
    cpu_delta = int((cpu.get("cpu_usage") or {}).get("total_usage") or 0) - int(
        (pre.get("cpu_usage") or {}).get("total_usage") or 0
    )
    system_delta = int(cpu.get("system_cpu_usage") or 0) - int(pre.get("system_cpu_usage") or 0)
    online = int(cpu.get("online_cpus") or 0) or len(
        (cpu.get("cpu_usage") or {}).get("percpu_usage") or []
    ) or 1
    if cpu_delta <= 0 or system_delta <= 0:
        return 0.0
    return round(cpu_delta / system_delta * online * 100.0, 2)


def _memory(stats: dict) -> tuple[int, int]:
    memory = stats.get("memory_stats") or {}
    usage = int(memory.get("usage") or 0)
    cache = int((memory.get("stats") or {}).get("inactive_file") or 0)
    limit = int(memory.get("limit") or 0)
    return max(0, usage - cache), max(0, limit)


def collect_containers(include_all: bool = False) -> dict:
    rows = docker_get("/containers/json?all=1")
    result: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        names = [str(name).lstrip("/") for name in row.get("Names") or []]
        name = names[0] if names else str(row.get("Id") or "")[:12]
        if not include_all and name not in DEFAULT_NAMES:
            continue
        container_id = str(row.get("Id") or "")
        inspect = docker_get(f"/containers/{container_id}/json")
        stats = docker_get(f"/containers/{container_id}/stats?stream=false")
        state = inspect.get("State") or {}
        health = (state.get("Health") or {}).get("Status")
        memory_used, memory_limit = _memory(stats if isinstance(stats, dict) else {})
        result.append(
            {
                "id": container_id[:12],
                "name": name,
                "image": row.get("Image"),
                "state": state.get("Status") or row.get("State") or "unknown",
                "health": health or "none",
                "started_at": state.get("StartedAt"),
                "restart_count": int(inspect.get("RestartCount") or 0),
                "cpu_percent": _cpu_percent(stats if isinstance(stats, dict) else {}),
                "memory_used_bytes": memory_used,
                "memory_limit_bytes": memory_limit,
            }
        )
    result.sort(key=lambda item: item["name"])
    version = docker_get("/version")
    return {
        "available": True,
        "docker_version": (version or {}).get("Version") if isinstance(version, dict) else None,
        "containers": result,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OCI-NT-Monitor-Agent/1.0"

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            try:
                version = docker_get("/version")
                self._send(200, {"status": "ok", "service": "oci-nt-monitor-agent", "version": "2.0.0", "docker_version": version.get("Version")})
            except Exception as exc:
                self._send(503, {"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            return
        if parsed.path == "/containers":
            include_all = parse_qs(parsed.query).get("all", ["0"])[0] == "1"
            try:
                self._send(200, collect_containers(include_all=include_all))
            except Exception as exc:
                self._send(503, {"available": False, "error": f"{type(exc).__name__}: {exc}", "containers": []})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        self._send(405, {"error": "read only"})

    def do_PUT(self) -> None:  # noqa: N802
        self._send(405, {"error": "read only"})

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(405, {"error": "read only"})

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
