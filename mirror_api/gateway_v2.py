"""Opt-in v2 GET adapter. No launcher change and no activation of the pilot.

create_server takes an existing MirrorCore; ownership/lifecycle stays with its
creator. TLS, private-file validation and fixed port reuse the v1 factory.
"""
from http import HTTPStatus
import re
from urllib.parse import parse_qsl

from .gateway_readonly import ReadOnlyHandler, TRUSTED_HTTP_HOSTS, create_server as v1_server
from .read_v2 import CoreReadV2, ReadError

REPOSITORY = re.compile(r'/gateway/v2/repositories/([A-Za-z0-9_][A-Za-z0-9_.-]{0,119})(?:/(worktrees|github|actions))?\Z')
PATHS = {None: 'get_repository_v2', 'worktrees': 'list_worktrees_v2',
         'github': 'get_github_status_v2', 'actions': 'get_available_actions_v2'}


def route(raw):
    """No normalization: ambiguous or encoded identities are never resolved."""
    if (type(raw) is not str or len(raw.encode()) > 4096 or
            any(ord(c) < 33 or ord(c) > 126 for c in raw) or
            any(c in raw for c in ('%', '#', '\\'))):
        raise ReadError('SCHEMA_INVALID')
    path, _, query = raw.partition('?')
    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=5)
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ReadError('SCHEMA_INVALID')
    if path in ('/gateway/v2/repositories', '/gateway/v2/capabilities'):
        operation = ('list_repositories_v2' if path.endswith('repositories') else
                     'get_capabilities_v2')
        target, scope = values.pop('inventory', None), values.pop('scope_digest', None)
        if target is None or scope is None:
            raise ReadError('SCHEMA_INVALID')
    else:
        found = REPOSITORY.fullmatch(path)
        if not found:
            raise ReadError('REPOSITORY_NOT_ALLOWED')
        target, suffix = found.groups()
        operation, scope = PATHS[suffix], None
    params = {}
    if operation in ('list_repositories_v2', 'list_worktrees_v2', 'get_github_status_v2'):
        if not {'offset', 'limit'} <= set(values) <= {'offset', 'limit', 'revision'}:
            raise ReadError('SCHEMA_INVALID')
        for field in ('offset', 'limit'):
            if not re.fullmatch(r'0|[1-9][0-9]{0,4}', values[field]):
                raise ReadError('SCHEMA_INVALID')
        params = {'offset': int(values['offset']), 'limit': int(values['limit']),
                  'inventoryRevision': values.get('revision')}
    elif values:
        raise ReadError('SCHEMA_INVALID')
    return operation, target, params, scope


class ReadV2Handler(ReadOnlyHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def do_GET(self):
        if not self.path.startswith('/gateway/v2/'):
            super().do_GET()
            return
        if not self.authenticated():
            self.send_json(HTTPStatus.UNAUTHORIZED, {'error': 'GATEWAY_AUTH_REQUIRED'})
            return
        hosts = self.headers.get_all('Host', [])
        lengths = self.headers.get_all('Content-Length', [])
        header_bytes = sum(len(key) + len(value) + 4 for key, value in self.headers.items())
        if (len(self.path.encode()) + header_bytes > 4096 or len(hosts) != 1 or hosts[0] not in TRUSTED_HTTP_HOSTS or
                self.headers.get_all('Transfer-Encoding') or len(lengths) > 1 or
                lengths and lengths != ['0'] or any(self.headers.get_all(name) for name in
                    ('Cookie', 'Origin', 'X-CSRF-Token', 'X-Mirror-CSRF'))):
            self.close_connection = True
            self.send_json(HTTPStatus.FORBIDDEN, {'error': 'GATEWAY_REQUEST_DENIED'})
            return
        try:
            operation, target, params, scope = route(self.path)
            result = self.server.read_v2.call(operation, target, params, inventory_scope=scope)
        except (ValueError, TypeError, UnicodeError):
            self.send_json(HTTPStatus.BAD_REQUEST, {'error': 'GATEWAY_ROUTE_DENIED'})
            return
        # Domain failures are typed envelopes, including revision conflicts.
        # Malformed ingress/authentication failures never reach the Core.
        self.send_json(HTTPStatus.OK, result)


def create_server(core, *, bind, cert, key, secret_file, allowed_repos, inventories):
    reads = CoreReadV2(core, allowed_repos=allowed_repos, inventories=inventories)
    server = v1_server(core, bind=bind, cert=cert, key=key, secret_file=secret_file,
                       allowed_repos=allowed_repos)
    server.read_v2 = reads
    server.RequestHandlerClass = ReadV2Handler
    return server
