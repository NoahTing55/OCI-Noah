from __future__ import annotations

import http.client
import os
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

SOCKET_PATH = os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
LISTEN_HOST = os.getenv("DOCKER_GUARD_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("DOCKER_GUARD_PORT", "9861"))
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

ALLOWED_PATHS = (
    re.compile(r"^/version$"),
    re.compile(r"^/containers/json\?all=1$"),
    re.compile(r"^/containers/[a-f0-9]{12,64}/json$"),
    re.compile(r"^/containers/[a-f0-9]{12,64}/stats\?stream=false$"),
)


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 4.0):
        super().__init__("localhost", timeout=timeout)
        self.unix_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.unix_path)
        self.sock = sock


def allowed_target(raw_target: str) -> bool:
    if len(raw_target) > 256 or "\\" in raw_target:
        return False
    parsed = urlsplit(raw_target)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return False
    canonical = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return any(pattern.fullmatch(canonical) for pattern in ALLOWED_PATHS)


class Handler(BaseHTTPRequestHandler):
    server_version = "OCI-NT-Docker-Guard/1.0"

    def send_plain(self, status: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_plain(200, "ok")
            return
        if not allowed_target(self.path):
            self.send_plain(403, "docker endpoint denied")
            return
        upstream = UnixHTTPConnection(SOCKET_PATH)
        try:
            upstream.request("GET", self.path, headers={"Host": "localhost"})
            response = upstream.getresponse()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                self.send_plain(502, "docker response too large")
                return
            self.send_response(response.status)
            self.send_header(
                "Content-Type",
                response.getheader("Content-Type", "application/json"),
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, http.client.HTTPException) as exc:
            self.send_plain(502, f"docker unavailable: {type(exc).__name__}")
        finally:
            upstream.close()

    def do_POST(self) -> None:  # noqa: N802
        self.send_plain(405, "method not allowed")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
