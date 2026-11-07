"""Loopback-only authenticated Mirror browser API (Python stdlib)."""
import argparse
import hmac
import hashlib
import ssl
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import webbrowser
from urllib.parse import parse_qs, urlsplit
from .operations import Mirror, ObservationError, REPO, set_preview_source
from .pull import PullWorker, PullDenied

STATIC = Path(__file__).parent / "static"


class MirrorServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address, mirror):
        if address[0] != "127.0.0.1":
            raise ValueError("LOOPBACK_ONLY")
        self.mirror = mirror
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.sessions = set()
        self.csrf_key = secrets.token_bytes(32)
        self.lock = threading.Lock()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "FortisMirror/0.1"

    def log_message(self, format, *args):
        # Deliberately no request URLs, session secrets or tokens in logs.
        return

    def send(self, status, data, content_type="application/json", extra=None):
        payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authenticated(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        item = cookie.get("mirror_session")
        if item is None:
            return False
        with self.server.lock:
            return item.value in self.server.sessions

    def session_id(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        item = cookie.get("mirror_session")
        if item is None:
            return None
        with self.server.lock:
            return item.value if item.value in self.server.sessions else None

    def csrf(self, session):
        return hmac.new(self.server.csrf_key, session.encode('ascii'), hashlib.sha256).hexdigest()

    def do_GET(self):
        # Bound to loopback, but still validate Host to prevent DNS rebinding.
        if self.headers.get("Host") not in (f"127.0.0.1:{self.server.server_port}",):
            self.send(HTTPStatus.FORBIDDEN, {"error": "HOST_DENIED"})
            return
        url = urlsplit(self.path)
        if url.path == "/session":
            values = parse_qs(url.query, strict_parsing=True)
            supplied = values.get("boot", [])
            if len(supplied) != 1 or not hmac.compare_digest(supplied[0], self.server.bootstrap_token):
                self.send(HTTPStatus.FORBIDDEN, {"error": "BOOTSTRAP_DENIED"})
                return
            token = secrets.token_urlsafe(32)
            with self.server.lock:
                self.server.sessions.add(token)
                self.server.bootstrap_token = secrets.token_urlsafe(32)
            self.send(HTTPStatus.FOUND, b"", "text/plain", {
                "Location": "/", "Set-Cookie": f"mirror_session={token}; Path=/; HttpOnly; SameSite=Strict"})
            return
        if not self.authenticated():
            self.send(HTTPStatus.UNAUTHORIZED, {"error": "SESSION_REQUIRED"})
            return
        if url.path == "/api/v1/csrf" and not url.query:
            self.send(HTTPStatus.OK, {"csrf": self.csrf(self.session_id())})
            return
        if url.path.startswith("/api/v1/docker-actions/"):
            self.send(HTTPStatus.NOT_FOUND, {"error": "GATEWAY_ONLY_GIT_WRITES"})
            return
        if url.path.startswith("/api/v1/repositories/"):
            name = url.path.removeprefix("/api/v1/repositories/")
            if not REPO.fullmatch(name) or url.query not in ("", "remote=true"):
                self.send(HTTPStatus.BAD_REQUEST, {"error": "REPOSITORY_QUERY_INVALID"})
                return
            try:
                data = self.server.mirror.inventory(remote=url.query=="remote=true", repo=name)
            except ObservationError as exc:
                self.send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
                return
            if not data["repositories"]:
                self.send(HTTPStatus.NOT_FOUND, {"error": "REPOSITORY_NOT_FOUND"})
                return
            self.send(HTTPStatus.OK, {"schemaVersion":1,"repository":data["repositories"][0],
                                      "checkedAt":data["checkedAt"]})
            return
        if url.path == "/api/v1/repositories":
            if url.query not in ("", "remote=true"):
                self.send(HTTPStatus.BAD_REQUEST, {"error": "QUERY_DENIED"})
                return
            try:
                data = self.server.mirror.inventory(remote=url.query == "remote=true")
            except ObservationError as exc:
                self.send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "repositories": None})
                return
            self.send(HTTPStatus.OK, data)
            return
        if url.path == "/api/v1/capabilities":
            self.send(HTTPStatus.OK, {"schemaVersion": 1, "readOnly": True,
                "operations": ["list_repositories", "get_repository_status", "list_worktrees", "get_available_actions"],
                "pullMain": "GATEWAY_ASK_SOURCE_ONLY_NOT_ACTIVATED", "ownerGitRepos": []})
            return
        if url.query:
            self.send(HTTPStatus.BAD_REQUEST, {"error": "QUERY_DENIED"})
            return
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8")}
        if url.path not in files:
            self.send(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})
            return
        name, content_type = files[url.path]
        self.send(HTTPStatus.OK, (STATIC / name).read_bytes(), content_type)

    def do_POST(self):
        if not self.authenticated():
            self.send(HTTPStatus.UNAUTHORIZED, {"error": "SESSION_REQUIRED"})
            return
        self.send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "GATEWAY_ONLY_GIT_WRITES"})

    do_PUT = do_PATCH = do_DELETE = do_POST


class GatewayHandler(Handler):
    """Separate TLS-only Gateway route surface; browser cookie is never accepted."""
    def authorized(self):
        token = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.gateway_secret
        return hmac.compare_digest(token, expected)

    def identity(self, path):
        prefix = "/gateway/v1/repositories/"
        if not path.startswith(prefix):
            return None
        suffix = path[len(prefix):]
        name = suffix.split("/")[0]
        if not REPO.fullmatch(name) or name not in self.server.allowed_repos:
            return None
        return name, suffix[len(name):]

    def do_GET(self):
        if not self.authorized():
            self.send(HTTPStatus.UNAUTHORIZED, {"error": "GATEWAY_AUTH_REQUIRED"})
            return
        url = urlsplit(self.path)
        ident = self.identity(url.path)
        if url.query or ident is None:
            self.send(HTTPStatus.NOT_FOUND, {"error": "GATEWAY_ROUTE_DENIED"})
            return
        repo_id, remainder = ident
        if remainder.startswith("/pull-results/"):
            request_id = remainder.removeprefix("/pull-results/")
            try:
                result = self.server.worker.status(request_id, repo_id=repo_id)
            except PullDenied as exc:
                self.send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self.send(HTTPStatus.OK, result)
            return
        if remainder:
            self.send(HTTPStatus.NOT_FOUND, {"error": "GATEWAY_ROUTE_DENIED"})
            return
        try:
            data = self.server.mirror.inventory(remote=False)
        except ObservationError as exc:
            self.send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
            return
        matches = [item for item in data["repositories"] if item["repoId"] == repo_id]
        if len(matches) != 1:
            self.send(HTTPStatus.NOT_FOUND, {"error": "REPOSITORY_NOT_FOUND"})
            return
        self.send(HTTPStatus.OK, {"schemaVersion": 1, "repository": matches[0]})

    def do_POST(self):
        if not self.authorized():
            self.send(HTTPStatus.UNAUTHORIZED, {"error": "GATEWAY_AUTH_REQUIRED"})
            return
        url = urlsplit(self.path)
        ident = self.identity(url.path)
        if url.query or ident is None or ident[1] != "/pull-main":
            self.send(HTTPStatus.NOT_FOUND, {"error": "GATEWAY_ROUTE_DENIED"})
            return
        if self.headers.get("Content-Type") != "application/json":
            self.send(HTTPStatus.BAD_REQUEST, {"error": "JSON_REQUIRED"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError()
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError()
            def strict(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result
            payload = json.loads(raw, object_pairs_hook=strict)
            if payload.get("requestId") != self.headers.get("Idempotency-Key"):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, AttributeError):
            self.send(HTTPStatus.BAD_REQUEST, {"error": "PULL_REQUEST_INVALID"})
            return
        try:
            result = self.server.worker.run(payload, repo_id=ident[0], allowed_repos=self.server.allowed_repos)
        except PullDenied as exc:
            self.send(HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        self.send(HTTPStatus.OK, result)

    do_PUT = do_PATCH = do_DELETE = Handler.do_POST


def gateway_server(mirror, bind, port, cert, key, secret_file, allowed_repos, receipt_db):
    if not cert or not key or not secret_file or not allowed_repos:
        raise ValueError("GATEWAY_TLS_AUTH_CONFIG_REQUIRED")
    token = Path(secret_file).read_text(encoding="ascii").strip()
    if len(token) < 32 or len(token) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("GATEWAY_SECRET_INVALID")
    server = ThreadingHTTPServer((bind, port), GatewayHandler)
    server.daemon_threads = True
    server.mirror = mirror
    server.gateway_secret = token
    server.allowed_repos = frozenset(allowed_repos)
    server.worker = PullWorker(mirror, receipt_db)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert, keyfile=key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server

def open_local_browser(port, boot_token, *, opener=None, delay=0.4):
    """Open only this process's loopback bootstrap URL, never external hosts.

    The HTTP socket has been bound by MirrorServer; a short daemon timer gives
    serve_forever time to accept the browser's initial session request. A
    browser failure leaves the printed one-time URL available in the console.
    """
    url = f"http://127.0.0.1:{port}/session?boot={boot_token}"

    def invoke():
        try:
            if not (opener or webbrowser.open)(url):
                print("Browser did not open automatically; use the one-time URL above.", flush=True)
        except Exception:
            print("Browser did not open automatically; use the one-time URL above.", flush=True)

    timer = threading.Timer(delay, invoke)
    timer.daemon = True
    timer.start()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-root", default=str(Path.home() / "Repos"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true",
                        help="Open the one-time 127.0.0.1 session URL in the default browser")
    parser.add_argument("--preview-source-task", default=None,
                        help="Read-only developer preview: use exporter sources in this Docker task worktree")
    parser.add_argument("--gateway-listen", action="store_true")
    parser.add_argument("--gateway-bind", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=443)
    parser.add_argument("--gateway-cert")
    parser.add_argument("--gateway-key")
    parser.add_argument("--gateway-secret-file")
    parser.add_argument("--gateway-repo", action="append", default=[])
    parser.add_argument("--gateway-receipt-db")
    args = parser.parse_args(argv)
    if not 1024 <= args.port < 65536:
        parser.error("port must be 1024..65535")
    if args.preview_source_task is not None:
        if args.gateway_listen:
            parser.error("preview is read-only: owner Git writes and Gateway listener are disabled")
        try:
            set_preview_source(args.preview_source_task)
        except ObservationError:
            parser.error("invalid preview source task")
    server = MirrorServer(("127.0.0.1", args.port), Mirror(args.windows_root))
    external = None
    if args.gateway_listen:
        if not all(REPO.fullmatch(x) for x in args.gateway_repo):
            parser.error("invalid gateway repo allowlist")
        if not args.gateway_receipt_db:
            parser.error("gateway receipt database path required")
        external = gateway_server(server.mirror, args.gateway_bind, args.gateway_port,
            args.gateway_cert, args.gateway_key, args.gateway_secret_file,
            args.gateway_repo, args.gateway_receipt_db)
        threading.Thread(target=external.serve_forever, daemon=True).start()
    print(f"Open locally: http://127.0.0.1:{args.port}/session?boot={server.bootstrap_token}", flush=True)
    if args.open_browser:
        open_local_browser(server.server_port, server.bootstrap_token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if external is not None:
            external.shutdown()
            external.server_close()
        server.server_close()


if __name__ == "__main__":
    main()
