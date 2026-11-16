"""Loopback-only authenticated Mirror browser API (Python stdlib)."""
import argparse
import hmac
import hashlib
import ssl
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
import secrets
import threading
import webbrowser
from urllib.parse import parse_qs, urlsplit
from .operations import Mirror, ObservationError, REPO, set_preview_source
from .pull import PullWorker, PullDenied
from .browser_pull import BrowserPull

STATIC = Path(__file__).parent / "static"


class MirrorServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address, mirror, *, allow_browser_pull=True, receipt_db=None):
        if address[0] != "127.0.0.1":
            raise ValueError("LOOPBACK_ONLY")
        self.mirror = mirror
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.sessions = set()
        self.csrf_key = secrets.token_bytes(32)
        self.lock = threading.Lock()
        self.allow_browser_pull = bool(allow_browser_pull)
        self.browser_pull = BrowserPull(mirror, receipt_db)
        self.repo_actions = set()
        self.repo_actions_guard = threading.Lock()
        super().__init__(address, Handler)

    def claim_repo_action(self, repo_id):
        with self.repo_actions_guard:
            if repo_id in self.repo_actions:
                return False
            self.repo_actions.add(repo_id)
            return True

    def release_repo_action(self, repo_id):
        with self.repo_actions_guard:
            self.repo_actions.discard(repo_id)


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

    def allowed_host(self):
        # Binding to loopback is not sufficient against DNS rebinding.
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.allowed_host():
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
        # A readback requires the same authenticated session that authorized
        # the preview; receipt IDs are not general capabilities.
        match = re.fullmatch(r"/api/v1/repositories/([A-Za-z0-9_][A-Za-z0-9_.-]{0,119})/pull-results/([0-9a-f-]{36})", url.path)
        if match and not url.query:
            try:
                result = self.server.browser_pull.status(match.group(1), self.session_id(), match.group(2))
            except PullDenied as exc:
                self.send(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            self.send(HTTPStatus.OK, result)
            return
        if url.path.startswith("/api/v1/repositories/"):
            name = url.path.removeprefix("/api/v1/repositories/")
            if not REPO.fullmatch(name) or url.query not in ("", "remote=true", "remote=details"):
                self.send(HTTPStatus.BAD_REQUEST, {"error": "REPOSITORY_QUERY_INVALID"})
                return
            try:
                data = self.server.mirror.inventory(remote=url.query in ("remote=true", "remote=details"),
                    repo=name, **({"details": True} if url.query == "remote=details" else {}))
                if data['repositories'] and self.server.allow_browser_pull:
                    data['repositories'][0]['pullSafety'] = self.server.browser_pull.safety(name)
            except (ObservationError, PullDenied) as exc:
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
            local_pull = self.server.allow_browser_pull and self.server.browser_pull.configured
            operations = ["list_repositories", "get_repository_status", "list_worktrees", "get_available_actions"]
            if local_pull:
                operations += ["check_windows_history", "preview_windows_pull", "execute_windows_pull", "read_windows_pull_receipt"]
            self.send(HTTPStatus.OK, {"schemaVersion": 1,
                "readOnly": not local_pull,
                "operations": operations,
                "browserPull": {"configured": local_pull,
                                "mode": "local_owner_confirm", "previewRequired": True},
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
        if not self.allowed_host():
            self.send(HTTPStatus.FORBIDDEN, {"error": "HOST_DENIED"})
            return
        if not self.authenticated():
            self.send(HTTPStatus.UNAUTHORIZED, {"error": "SESSION_REQUIRED"})
            return
        url = urlsplit(self.path)
        match = re.fullmatch(r"/api/v1/repositories/([A-Za-z0-9_][A-Za-z0-9_.-]{0,119})/(check-windows|pull-preview|pull-execute)", url.path)
        if not match or url.query:
            self.send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "GATEWAY_ONLY_GIT_WRITES"})
            return
        if not self.server.allow_browser_pull:
            self.send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "BROWSER_PULL_PREVIEW_MODE_DISABLED"})
            return
        # Metadata fetch writes Git's objects/FETCH_HEAD; never expose it from
        # read-only preview mode or without durable owner-local worker setup.
        if match.group(2) == 'check-windows' and not self.server.browser_pull.configured:
            self.send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "OWNER_CHECK_NOT_CONFIGURED"})
            return
        # Origin and session-derived CSRF token both required; neither an
        # external website nor a stolen previewId is enough to trigger Git.
        expected_origin = f"http://127.0.0.1:{self.server.server_port}"
        supplied_csrf = self.headers.get("X-CSRF-Token", "")
        if (self.headers.get("Origin") != expected_origin or
                self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none") or
                not re.fullmatch(r"[0-9a-f]{64}", supplied_csrf) or
                not hmac.compare_digest(supplied_csrf, self.csrf(self.session_id()))):
            self.send(HTTPStatus.FORBIDDEN, {"error": "BROWSER_ORIGIN_OR_CSRF_DENIED"})
            return
        if self.headers.get("Content-Type") != "application/json" or self.headers.get("Transfer-Encoding"):
            self.send(HTTPStatus.BAD_REQUEST, {"error": "PULL_JSON_REQUIRED"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024:
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
            body = json.loads(raw, object_pairs_hook=strict)
            if type(body) is not dict:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            self.send(HTTPStatus.BAD_REQUEST, {"error": "PULL_JSON_INVALID"})
            return
        repo_id, action = match.groups()
        session = self.session_id()
        # An owner Check may fetch Git metadata. Never overlap its writes with
        # Pull Execute on the same clone, even after browser fetch abort; other
        # repositories are independent. Preview remains observation-only.
        locked = action in ('check-windows', 'pull-execute')
        if locked and not self.server.claim_repo_action(repo_id):
            self.send(HTTPStatus.CONFLICT, {"error": "REPOSITORY_OPERATION_PENDING"})
            return
        try:
            if action == 'check-windows':
                if body:
                    raise PullDenied('OWNER_CHECK_FIELDS_INVALID')
                # The owner-local and separate Gateway listeners can share one
                # PullWorker. Its per-clone lock also fences a metadata fetch
                # against a Gateway Pull for the same Windows Git directory.
                worker_lock = self.server.browser_pull._worker()._repo_lock(repo_id)
                if not worker_lock.acquire(blocking=False):
                    raise PullDenied('REPOSITORY_OPERATION_PENDING')
                try:
                    safety = self.server.browser_pull.safety(repo_id)
                    # An unresolved effect permits observation, never another
                    # metadata fetch, including after owner session restart.
                    data = (self.server.mirror.inventory(remote=True, repo=repo_id) if safety['blocked']
                            else self.server.mirror.check_repository(repo_id))
                    if len(data.get('repositories', [])) != 1 or data['repositories'][0].get('repoId') != repo_id:
                        raise PullDenied('DOCKER_REPOSITORY_NOT_REGISTERED')
                    result = {"schemaVersion": 1, "checkedAt": data['checkedAt'],
                              "repository": data['repositories'][0]}
                    result['repository']['pullSafety'] = safety
                finally:
                    worker_lock.release()
            elif action == "pull-preview":
                if body:
                    raise PullDenied("PULL_PREVIEW_FIELDS_INVALID")
                result = self.server.browser_pull.preview(repo_id, session)
            else:
                if set(body) != {"previewId", "confirm"}:
                    raise PullDenied("PULL_EXECUTE_FIELDS_INVALID")
                result = self.server.browser_pull.execute(repo_id, session, body["previewId"], body["confirm"])
        except (PullDenied, ObservationError) as exc:
            self.send(HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        finally:
            if locked:
                self.server.release_repo_action(repo_id)
        self.send(HTTPStatus.OK, result)

    def do_PUT(self):
        self.send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "METHOD_DENIED"})

    do_PATCH = do_DELETE = do_PUT


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

    do_PUT = do_PATCH = do_DELETE = Handler.do_PUT


def gateway_server(mirror, bind, port, cert, key, secret_file, allowed_repos, receipt_db, *, shared_worker=None):
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
    server.worker = shared_worker if shared_worker is not None else PullWorker(mirror, receipt_db)
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
    server = MirrorServer(("127.0.0.1", args.port), Mirror(args.windows_root),
                          allow_browser_pull=args.preview_source_task is None,
                          receipt_db=args.gateway_receipt_db if args.gateway_listen else None)
    external = None
    if args.gateway_listen:
        if not all(REPO.fullmatch(x) for x in args.gateway_repo):
            parser.error("invalid gateway repo allowlist")
        if not args.gateway_receipt_db:
            parser.error("gateway receipt database path required")
        external = gateway_server(server.mirror, args.gateway_bind, args.gateway_port,
            args.gateway_cert, args.gateway_key, args.gateway_secret_file,
            args.gateway_repo, args.gateway_receipt_db,
            shared_worker=server.browser_pull._worker())
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
