"""P4 OFFLINE-ONLY WRITE ingress boundary. NO HTTP handler or effect path.

The existing Gateway listeners remain GET-only. This module only validates a
candidate HTTP request handed to it by *future* owner-reviewed, TLS-only code.
It does not authenticate a human actor, prove ASK, verify an Ed25519 signature,
admit a machine effect, bind a receipt or call PullWorker. The body actor and
claims remain UNTRUSTED until the separate pinned signature / persisted-preview
checks complete.

Future listener MUST reject Content-Length > MAX_BODY before reading the socket,
validate the exact TLS interface/client source, never log bearer/body, and keep
this gate/owner profile private. Never fall back to a READ bearer or v1 POST.
"""
from dataclasses import dataclass
import hmac
import json
import re

from .proof_v2 import ACTOR, CLAIMS, DIGEST, KEY_ID, OUTER, PROOF, REPO, SHA, UUID4, PinnedEd25519Keys
from .read_v2 import ReadError, require

MAX_BODY = 4096
MAX_META = 4096
HOSTS = frozenset({'host.docker.internal', 'host.docker.internal:443'})
PATH = re.compile(r'/gateway/v2/repositories/([A-Za-z0-9_][A-Za-z0-9_.-]{0,119})/pull\Z')
TOKEN = re.compile(r'[\x21-\x7e]{32,256}\Z')
ALLOWED_HEADERS = frozenset(('host', 'authorization', 'content-type', 'content-length',
                             'accept', 'user-agent', 'connection', 'idempotency-key',
                             'x-idempotency-key'))


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('DUPLICATE_KEY')
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError('NONFINITE')


def _invalid_json_float(_value):
    raise ValueError('FLOAT_DENIED')


@dataclass(frozen=True)
class UntrustedWriteCandidate:
    """Immutable request bytes. NOT a trusted actor or an authorization permit."""
    repo_id: str
    request_id: str
    raw_body: bytes

    def __repr__(self):
        return (f'<UntrustedWriteCandidate repoId={self.repo_id!r} '
                f'requestId={self.request_id!r} body=REDACTED>')

    def untrusted_dispatch(self):
        # Fresh object per call, never a durable/authorized identity.
        return json.loads(self.raw_body, object_pairs_hook=_no_duplicates,
                          parse_constant=_invalid_json_constant,
                          parse_float=_invalid_json_float)


class WriteIngressProfile:
    """Owner-installed in-memory service credentials and owner signing trust.

    No file loader, key generator, token provider or launcher is included here.
    READ and WRITE bearer values must be distinct. The owner-pinned profile
    must already be bound to a revisioned machine generation (PR #39).
    """

    def __init__(self, *, read_bearer, write_bearer, allowed_repos, signing_pins):
        require(type(read_bearer) is str and TOKEN.fullmatch(read_bearer) is not None and
                type(write_bearer) is str and TOKEN.fullmatch(write_bearer) is not None and
                read_bearer != write_bearer, 'PERMISSION_DENIED')
        require(type(allowed_repos) in (tuple, list, frozenset) and
                1 <= len(allowed_repos) <= 128 and
                all(type(repo) is str and REPO.fullmatch(repo)
                    for repo in allowed_repos) and
                len(set(allowed_repos)) == len(allowed_repos), 'REPOSITORY_NOT_ALLOWED')
        require(type(signing_pins) is PinnedEd25519Keys and
                signing_pins.revision is not None, 'UNKNOWN_KEY')
        # The bearer is only a SERVICE identity. Actor remains untrusted until
        # a valid owner-pinned signature and actor-scoped preview are verified.
        self._write_bearer = 'Bearer ' + write_bearer
        self.allowed_repos = frozenset(allowed_repos)
        self.signing_pins = signing_pins

    def __repr__(self):
        return '<WriteIngressProfile credentials=REDACTED>'

    def accept(self, method, raw_path, raw_headers, body):
        """Validate exact HTTP envelope; return UNTRUSTED signed payload only.

        raw_headers is a sequence of (name, value) pairs, not a dict: duplicate
        Authorization/Host/length and mixed-case duplicates must remain visible.
        Callers MUST enforce Content-Length <= 4096 *before* socket body read.
        """
        require(type(method) is str and method == 'POST' and
                type(raw_path) is str and len(raw_path) <= MAX_META and
                PATH.fullmatch(raw_path) is not None, 'SCHEMA_INVALID')
        repo_id = PATH.fullmatch(raw_path).group(1)
        require(repo_id in self.allowed_repos, 'REPOSITORY_NOT_ALLOWED')
        require(type(raw_headers) in (tuple, list) and
                1 <= len(raw_headers) <= 16, 'SCHEMA_INVALID')
        headers = {}
        size = len(raw_path)
        for pair in raw_headers:
            require(type(pair) in (tuple, list) and len(pair) == 2 and
                    all(type(part) is str and part.isascii() for part in pair),
                    'SCHEMA_INVALID')
            name, value = pair
            low = name.lower()
            require(re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}', name) and
                    low in ALLOWED_HEADERS and low not in headers and
                    all(32 <= ord(c) <= 126 for c in value), 'SCHEMA_INVALID')
            size += len(name) + len(value) + 4
            require(size <= MAX_META, 'LIMIT_EXCEEDED')
            headers[low] = value
        require(headers.get('host') in HOSTS, 'PERMISSION_DENIED')
        authorization = headers.get('authorization')
        require(type(authorization) is str and len(authorization) <= 300 and
                hmac.compare_digest(authorization, self._write_bearer),
                'PERMISSION_DENIED')
        require(headers.get('content-type') == 'application/json', 'SCHEMA_INVALID')
        length = headers.get('content-length')
        require(type(length) is str and re.fullmatch(r'[1-9][0-9]{0,3}', length) and
                0 < int(length) <= MAX_BODY, 'LIMIT_EXCEEDED')
        require(type(body) is bytes and len(body) == int(length), 'SCHEMA_INVALID')
        require(headers.get('accept', 'application/json') == 'application/json' and
                headers.get('connection', 'keep-alive').lower() in ('close', 'keep-alive'),
                'SCHEMA_INVALID')
        require(not ('idempotency-key' in headers and 'x-idempotency-key' in headers),
                'SCHEMA_INVALID')
        try:
            data = json.loads(body.decode('ascii'), object_pairs_hook=_no_duplicates,
                              parse_constant=_invalid_json_constant,
                              parse_float=_invalid_json_float)
        except (UnicodeError, ValueError, TypeError, RecursionError):
            raise ReadError('SCHEMA_INVALID') from None
        # Decode is intentionally only SHAPE validation: no actor becomes
        # authenticated until Ed25519 is verified against source owner pins.
        require(type(data) is dict and set(data) == OUTER, 'SCHEMA_INVALID')
        require(type(data['repoId']) is str and data['repoId'] == repo_id,
                'PERMISSION_DENIED')
        for field, rule in (('requestId', UUID4), ('previewId', UUID4),
                            ('actor', ACTOR), ('expectedHead', SHA), ('targetSha', SHA),
                            ('configGeneration', DIGEST)):
            require(type(data[field]) is str and rule.fullmatch(data[field]),
                    'SCHEMA_INVALID')
        require(data['expectedHead'] != data['targetSha'], 'SCHEMA_INVALID')
        proof = data['proof']
        require(type(proof) is dict and set(proof) == PROOF and
                type(proof['schemaVersion']) is int and proof['schemaVersion'] == 1 and
                type(proof['keyId']) is str and KEY_ID.fullmatch(proof['keyId']) and
                type(proof['signature']) is str, 'PROOF_REQUIRED')
        claims = proof['claims']
        require(type(claims) is dict and set(claims) == CLAIMS, 'SCHEMA_INVALID')
        for key in ('requestId', 'repoId', 'actor', 'previewId', 'expectedHead',
                    'targetSha', 'configGeneration'):
            require(type(claims[key]) is str and claims[key] == data[key],
                    'SCHEMA_INVALID')
        for field in ('issuedAt', 'expiresAt'):
            require(type(claims[field]) is int, 'SCHEMA_INVALID')
        for key in ('idempotency-key', 'x-idempotency-key'):
            if key in headers:
                require(headers[key] == data['requestId'], 'SCHEMA_INVALID')
        return UntrustedWriteCandidate(repo_id, data['requestId'], body)
