"""Isolated HTTPS READ-only Mirror Gateway listener, without any Git write route.

Only authenticated GET /gateway/v1/repositories/<registered-id> exists.
Never import PullWorker, owner/browser sessions, or a credential vault.
"""
import argparse
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import ssl
from urllib.parse import urlsplit

from .operations import Mirror, ObservationError, REPO

ROUTE = re.compile(r"/gateway/v1/repositories/([A-Za-z0-9_][A-Za-z0-9_.-]{0,119})\Z")
TRUSTED_HTTP_HOSTS = frozenset({"host.docker.internal", "host.docker.internal:443"})
MAX_RESULT_BYTES = 262144


class ReadOnlyHandler(BaseHTTPRequestHandler):
    server_version = "FortisMirrorRead/1"
    sys_version = ""

    def log_message(self, format, *args):
        # Never log target URLs or Bearer token.
        return

    def send_json(self, code, document):
        payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_RESULT_BYTES:
            code = HTTPStatus.SERVICE_UNAVAILABLE
            payload = b'{"error":"MIRROR_RESPONSE_TOO_LARGE"}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authenticated(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1 or len(values[0]) > 300:
            return False
        return hmac.compare_digest(values[0], "Bearer " + self.server.gateway_secret)

    def do_GET(self):
        if not self.authenticated():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "GATEWAY_AUTH_REQUIRED"})
            return
        if self.headers.get("Host") not in TRUSTED_HTTP_HOSTS:
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "GATEWAY_HOST_DENIED"})
            return
        url = urlsplit(self.path)
        match = ROUTE.fullmatch(url.path)
        if not match or url.query or url.fragment or match.group(1) not in self.server.allowed_repos:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "GATEWAY_ROUTE_DENIED"})
            return
        repo_id = match.group(1)
        try:
            # remote=False: no Gateway/GitHub calls, git fetch, HEAD mutation or Pull.
            snapshot = self.server.mirror.inventory(remote=False, repo=repo_id)
        except ObservationError:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "MIRROR_READ_UNAVAILABLE"})
            return
        if (type(snapshot) is not dict or snapshot.get("schemaVersion") != 1 or
                type(snapshot.get("repositories")) is not list or snapshot.get("errors")):
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "MIRROR_READ_INVALID"})
            return
        rows = snapshot["repositories"]
        if len(rows) != 1 or type(rows[0]) is not dict or rows[0].get("repoId") != repo_id:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "REPOSITORY_NOT_FOUND"})
            return
        self.send_json(HTTPStatus.OK, {"schemaVersion": 1, "repository": rows[0]})

    def deny_write(self):
        # No request body is ever parsed and no mutating worker is constructed.
        self.send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "GATEWAY_READ_ONLY"})

    do_POST = deny_write
    do_PUT = deny_write
    do_PATCH = deny_write
    do_DELETE = deny_write


def bind_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("GATEWAY_BIND_IP_INVALID") from None
    if (not isinstance(address, ipaddress.IPv4Address) or
            address.is_unspecified or address.is_multicast or address.is_reserved):
        raise ValueError("GATEWAY_BIND_IP_DENIED")
    return str(address)


def private_file(value):
    p = Path(value)
    source_root = Path(__file__).resolve().parent.parent
    if (not p.is_absolute() or p.is_symlink() or not p.is_file() or
            p.resolve().is_relative_to(source_root)):
        raise ValueError("GATEWAY_PRIVATE_FILE_INVALID")
    return p


def create_server(mirror, *, bind, cert, key, secret_file, allowed_repos):
    address = bind_ip(bind)
    if (type(allowed_repos) not in (list, tuple) or not allowed_repos or
            len(allowed_repos) != len(set(allowed_repos)) or
            not all(type(value) is str and REPO.fullmatch(value) for value in allowed_repos)):
        raise ValueError("GATEWAY_REPO_ALLOWLIST_INVALID")
    cert_path, key_path, secret_path = (
        private_file(value) for value in (cert, key, secret_file))
    token = secret_path.read_text(encoding="ascii").strip()
    if not 32 <= len(token) <= 256 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError("GATEWAY_SECRET_INVALID")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    # Fixed port 443 matches the only supported Gateway JSONAPI origin.
    server = ThreadingHTTPServer((address, 443), ReadOnlyHandler)
    server.daemon_threads = True
    server.mirror = mirror
    server.gateway_secret = token
    server.allowed_repos = frozenset(allowed_repos)
    try:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except Exception:
        server.server_close()
        raise
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-root", default=str(Path.home() / "Repos"))
    parser.add_argument("--bind", required=True, help="Exact reviewed Windows IPv4 interface; never 0.0.0.0")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--secret-file", required=True)
    parser.add_argument("--repo", action="append", required=True)
    args = parser.parse_args(argv)
    server = create_server(Mirror(args.windows_root), bind=args.bind,
                           cert=args.cert, key=args.key,
                           secret_file=args.secret_file, allowed_repos=args.repo)
    print("MIRROR_GATEWAY_READ_ONLY_HTTPS_READY", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
