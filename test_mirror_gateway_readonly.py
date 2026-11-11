"""No-auth/no-Pull negative acceptance for the isolated Mirror HTTPS read route."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from mirror_api.gateway_readonly import (
    ReadOnlyHandler, bind_ip, create_server, private_file)
from mirror_api.operations import ObservationError


class FakeMirror:
    def __init__(self):
        self.calls = []
        self.error = False
        self.rows = [{"repoId": "fixture", "docker": {"primary": {}, "worktrees": []},
                      "windows": {}, "github": {"error": "NOT_CHECKED"},
                      "actions": {"pull_main": {"available": False}},
                      "readTimingsMs": {}}]

    def inventory(self, *, remote=False, repo=None):
        self.calls.append((remote, repo))
        if self.error:
            raise ObservationError("DOCKER_FAILED")
        return {"schemaVersion": 1, "repositories": self.rows, "errors": []}


class ReadOnlyGatewayTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ReadOnlyHandler)
        self.server.gateway_secret = "a" * 48
        self.server.allowed_repos = frozenset({"fixture"})
        self.server.mirror = FakeMirror()
        self.server.daemon_threads = True
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(worker.join, 2)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, *, token="a" * 48, host="host.docker.internal",
                cookie=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Host": host}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=b"{}" if method != "GET" else None,
                     headers=headers)
        response = conn.getresponse()
        status = response.status
        payload = json.loads(response.read().decode())
        conn.close()
        return status, payload

    def test_only_fixed_authenticated_read_projects_real_schema(self):
        self.assertEqual(401, self.request("GET", "/gateway/v1/repositories/fixture",
                                           token=None, cookie="mirror_session=spoof")[0])
        self.assertEqual(401, self.request("GET", "/gateway/v1/repositories/fixture",
                                           token="wrong")[0])
        status, data = self.request("GET", "/gateway/v1/repositories/fixture")
        self.assertEqual(200, status)
        self.assertEqual({"schemaVersion", "repository"}, set(data))
        self.assertEqual("fixture", data["repository"]["repoId"])
        self.assertEqual([(False, "fixture")], self.server.mirror.calls)

    def test_scope_host_and_queries_are_denied_before_inventory(self):
        for path, host, expected in (
            ("/gateway/v1/repositories/other", "host.docker.internal", 404),
            ("/gateway/v1/repositories/fixture?remote=true", "host.docker.internal", 404),
            ("/gateway/v1/repositories/fixture/pull-results/123", "host.docker.internal", 404),
            ("/gateway/v1/repositories/fixture/../other", "host.docker.internal", 404),
            ("/gateway/v1/repositories/fixture", "evil.invalid", 403),
            ("/api/v1/repositories/fixture", "host.docker.internal", 404),
        ):
            with self.subTest(path=path):
                self.assertEqual(expected, self.request("GET", path, host=host)[0])
        self.assertEqual([], self.server.mirror.calls)

    def test_every_write_returns_405_and_never_invokes_mirror_or_worker(self):
        self.assertFalse(hasattr(self.server, "worker"))
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for route in ("/gateway/v1/repositories/fixture/pull-main",
                          "/gateway/v1/repositories/fixture"):
                with self.subTest(method=method, route=route):
                    status, data = self.request(method, route)
                    self.assertEqual(405, status)
                    self.assertEqual("GATEWAY_READ_ONLY", data["error"])
        self.assertEqual([], self.server.mirror.calls)

    def test_upstream_error_and_empty_inventory_are_not_healthy(self):
        self.server.mirror.error = True
        self.assertEqual(503, self.request("GET", "/gateway/v1/repositories/fixture")[0])
        self.server.mirror.error = False
        self.server.mirror.rows = []
        self.assertEqual(404, self.request("GET", "/gateway/v1/repositories/fixture")[0])

    def test_bind_and_private_path_denials(self):
        for value in ("0.0.0.0", "::", "abc", "224.0.0.1", "255.255.255.255"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bind_ip(value)
        self.assertEqual("127.0.0.1", bind_ip("127.0.0.1"))
        with self.assertRaises(ValueError):
            private_file("relative/path")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "secret"; src.write_text("a" * 48)
            link = root / "link"; link.symlink_to(src)
            with self.assertRaises(ValueError):
                private_file(link)

    def test_constructor_has_no_pull_worker_or_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            cert, key, token = (base / x for x in ("cert.pem", "key.pem", "token.txt"))
            cert.write_text("PUBLIC"); key.write_text("PRIVATE"); token.write_text("a"*48)
            context = mock.MagicMock()
            context.wrap_socket.side_effect = lambda sock, server_side: sock
            # Do not bind real port 443 in hosted CI; test the production
            # builder against an ephemeral TCP port without changing its code.
            def ephemeral(addr, handler):
                return ThreadingHTTPServer((addr[0], 0), handler)
            with mock.patch("mirror_api.gateway_readonly.ssl.SSLContext", return_value=context), \
                 mock.patch("mirror_api.gateway_readonly.ThreadingHTTPServer", side_effect=ephemeral):
                srv = create_server(FakeMirror(), bind="127.0.0.1",
                                    cert=str(cert), key=str(key), secret_file=str(token),
                                    allowed_repos=["fixture"])
            self.addCleanup(srv.server_close)
            self.assertFalse(hasattr(srv, "worker"))
            self.assertEqual({"fixture"}, srv.allowed_repos)
            self.assertEqual("a"*48, srv.gateway_secret)
            context.load_cert_chain.assert_called_once_with(
                certfile=str(cert), keyfile=str(key))
            self.assertFalse(any(base.glob("*.sqlite*")))


if __name__ == "__main__":
    unittest.main()
