"""End-to-end Unix Guard transport, with a fake Docker daemon and no Docker socket."""
import importlib
import os
from pathlib import Path
import socket
import socketserver
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
guard=importlib.import_module("app.docker_socket_guard")
monitor=importlib.import_module("app.monitor_agent")

class FakeDocker(socketserver.BaseRequestHandler):
    def handle(self):
        request=self.request.recv(4096)
        if request.startswith(b"GET /version HTTP/"):
            body=b'{"Version":"mock-ci"}'
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: "+str(len(body)).encode()+b"\r\nConnection: close\r\n\r\n"+body)
        else:
            body=b"unexpected"
            self.request.sendall(b"HTTP/1.1 500 Error\r\nContent-Length: 10\r\nConnection: close\r\n\r\n"+body)

class UnixGuardTransport(unittest.TestCase):
    def test_guard_exposes_only_allowlisted_get_over_unix(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            upstream=str(directory/"upstream.sock")
            exposed=str(directory/"guard.sock")
            fake=socketserver.UnixStreamServer(upstream,FakeDocker)
            server=guard.ThreadingUnixHTTPServer(exposed,guard.Handler)
            old_socket=guard.SOCKET_PATH
            old_monitor_socket=monitor.DOCKER_GUARD_UNIX_SOCKET
            old_monitor_host=monitor.DOCKER_GUARD_HOST
            guard.SOCKET_PATH=upstream
            monitor.DOCKER_GUARD_UNIX_SOCKET=exposed
            monitor.DOCKER_GUARD_HOST=""
            threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (fake,server)]
            try:
                for t in threads:t.start()
                self.assertEqual(monitor.docker_get("/version")["Version"],"mock-ci")
                for method,path,expected in (("GET","/containers/create",403),
                                             ("POST","/version",405),
                                             ("DELETE","/version",405)):
                    c=monitor.UnixHTTPConnection(exposed)
                    try:
                        c.request(method,path)
                        response=c.getresponse()
                        self.assertEqual(response.status,expected)
                        response.read()
                    finally:
                        c.close()
                self.assertFalse(os.path.exists("/var/run/docker.sock") and upstream == "/var/run/docker.sock")
            finally:
                monitor.DOCKER_GUARD_UNIX_SOCKET=old_monitor_socket
                monitor.DOCKER_GUARD_HOST=old_monitor_host
                guard.SOCKET_PATH=old_socket
                for item in (server,fake):
                    item.shutdown()
                    item.server_close()
                for t in threads:t.join(timeout=2)

if __name__=="__main__":
    unittest.main()
