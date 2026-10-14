"""Guard must translate a disconnected upstream Docker socket into HTTP 502."""
import http.client
import threading
import unittest
from http.server import HTTPServer
from unittest.mock import patch

from app import docker_socket_guard as guard


class DropUpstream:
    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        raise http.client.RemoteDisconnected("mock upstream disconnected")

    def close(self):
        pass


class GuardUpstreamDisconnectTests(unittest.TestCase):
    def test_upstream_disconnection_returns_502(self):
        server = HTTPServer(("127.0.0.1", 0), guard.Handler)
        worker = threading.Thread(target=server.handle_request, daemon=True)
        worker.start()
        try:
            with patch.object(guard, "UnixHTTPConnection", return_value=DropUpstream()):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                try:
                    conn.request("GET", "/version")
                    response = conn.getresponse()
                    body = response.read()
                finally:
                    conn.close()
            self.assertEqual(response.status, 502)
            self.assertIn(b"RemoteDisconnected", body)
        finally:
            server.server_close()
            worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
