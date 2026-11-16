"""P4 source-only Ed25519 verification for a *new* ASK dispatch candidate.

This module has no HTTP route, Git, lock or SQLite writer. An owner-pinned
active public-key set is supplied only by trusted Windows configuration, NEVER
by public params, incoming proof, remote discovery or bearer credentials.
The caller MUST reject duplicate/noncanonical JSON during HTTP ingress,
independently authenticate the READ/WRITE identities, retrieve
the actor-bound immutable preview, check fresh Git preconditions, and atomically
reserve the exact effect in the existing ledger before *any* Git operation.
An already-reserved UUID is handled by the future binding/receipt lookup path,
not by re-verifying an expired proof and not by dispatching again.

The Windows cryptography dependency is NOT installed or activated here. Missing
crypto support is a hard failure; activation requires a separate owner review.
"""
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re

from .read_v2 import ReadError, require

DOMAIN = b'FORTIS-MIRROR-ASK-V1\n'
KEY_ID = re.compile(r'[A-Za-z0-9_.-]{1,64}\Z')
ACTOR = re.compile(r'[A-Za-z0-9_.:-]{1,120}\Z')
REPO = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}\Z')
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
SIGNATURE = re.compile(r'[A-Za-z0-9_-]{86}\Z')
OUTER = frozenset({'requestId', 'repoId', 'actor', 'previewId', 'expectedHead',
                    'targetSha', 'configGeneration', 'proof'})
PROOF = frozenset({'schemaVersion', 'keyId', 'claims', 'signature'})
IDENTITY = frozenset({'issuer', 'audience', 'service', 'operation', 'requestId',
                      'actor', 'repoId', 'previewId', 'expectedHead',
                      'targetSha', 'configGeneration'})
CLAIMS = IDENTITY | {'fingerprint', 'issuedAt', 'expiresAt'}
MAX_UNIX = 4102444800


def _match(value, expression):
    return type(value) is str and expression.fullmatch(value) is not None


def _canonical(value):
    # Every claim value is independently checked ASCII; this is byte-for-byte
    # compatible with Gateway's canonical JSON on this closed identity schema.
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode('ascii')


class PinnedEd25519Keys:
    """Immutable snapshot of an *owner-configured* active public key inventory.

    Removal on owner revocation must cause a new Windows config generation, so
    unconsumed persisted previews cannot be reused with stale trust state.
    Do not construct from caller JSON or a remote key endpoint.
    """
    def __init__(self, active):
        require(type(active) is dict and len(active) <= 8, 'UNKNOWN_KEY')
        for key_id, key in active.items():
            require(_match(key_id, KEY_ID) and type(key) is bytes and len(key) == 32,
                    'UNKNOWN_KEY')
        self._active = dict(active)

    def get(self, key_id):
        require(_match(key_id, KEY_ID), 'UNKNOWN_KEY')
        value = self._active.get(key_id)
        require(value is not None, 'UNKNOWN_KEY')
        return value


@dataclass(frozen=True)
class VerifiedDispatch:
    """Validated claim only; this is NOT a durable reservation or permission."""
    request_id: str
    actor: str
    repo_id: str
    preview_id: str
    expected_head: str
    target_sha: str
    config_generation: str
    origin: str
    read_request_id: str
    expires_at: int


def _preview_epoch(value):
    require(type(value) is str and re.fullmatch(
        r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z|\+00:00)', value), 'PREVIEW_STALE')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0,
                'PREVIEW_STALE')
        result = int(dt.timestamp())
        require(0 < result <= MAX_UNIX, 'PREVIEW_STALE')
        return result
    except (OverflowError, ValueError):
        raise ReadError('PREVIEW_STALE') from None


def _verify_ed25519(public_key, signed_message, signature):
    # No fallback, dynamic key discovery, key generation or alternate algorithm.
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise ReadError('SOURCE_UNAVAILABLE') from None
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, signed_message)
    except (InvalidSignature, TypeError, ValueError):
        raise ReadError('SIGNATURE_INVALID') from None


def verify_new_dispatch(dispatch, *, authenticated_actor, route_repo, route_request_id,
                        persisted_preview, owner_pins, now):
    """Fail-closed pre-reservation proof check using trustworthy route context.

    `persisted_preview` is the result of MachineReads.persisted_preview_for_ask,
    already scoped by the authenticated actor, repository and current generation.
    The caller MUST check fresh HEAD/origin/current policy and MUST reserve in one
    ledger transaction; neither is implied by successful verification here.
    No claim in the incoming payload may override route/actor/owner trust.
    """
    require(type(now) is int and 0 <= now <= MAX_UNIX, 'SCHEMA_INVALID')
    require(isinstance(owner_pins, PinnedEd25519Keys), 'UNKNOWN_KEY')
    require(_match(authenticated_actor, ACTOR) and _match(route_repo, REPO) and
            _match(route_request_id, UUID4), 'PERMISSION_DENIED')
    require(type(dispatch) is dict and set(dispatch) == OUTER, 'SCHEMA_INVALID')
    for field, expression in [('requestId', UUID4), ('actor', ACTOR), ('repoId', REPO),
                              ('previewId', UUID4), ('expectedHead', SHA),
                              ('targetSha', SHA), ('configGeneration', DIGEST)]:
        require(_match(dispatch[field], expression), 'SCHEMA_INVALID')
    require(dispatch['requestId'] == route_request_id and
            dispatch['actor'] == authenticated_actor and
            dispatch['repoId'] == route_repo, 'PERMISSION_DENIED')
    require(dispatch['expectedHead'] != dispatch['targetSha'], 'SCHEMA_INVALID')
    require(type(persisted_preview) is dict and
            set(persisted_preview) == {'readRequestId', 'origin', 'snapshot'}, 'PREVIEW_STALE')
    snapshot = persisted_preview['snapshot']
    require(type(snapshot) is dict and _match(persisted_preview['readRequestId'], UUID4) and
            type(persisted_preview['origin']) is str and
            0 < len(persisted_preview['origin']) <= 512, 'PREVIEW_STALE')
    require(snapshot.get('blockers') == [] and snapshot.get('repoId') == route_repo and
            snapshot.get('originIdentity') == 'AlexPastukhh/' + route_repo and
            all(snapshot.get(field) == dispatch[field] for field in
                ('previewId', 'expectedHead', 'targetSha', 'configGeneration')) and
            snapshot.get('dockerHead') == dispatch['targetSha'], 'PREVIEW_STALE')
    preview_expires = _preview_epoch(snapshot.get('expiresAt'))
    require(now < preview_expires, 'PREVIEW_EXPIRED')
    proof = dispatch['proof']
    require(type(proof) is dict and set(proof) == PROOF, 'PROOF_REQUIRED')
    require(type(proof['schemaVersion']) is int and proof['schemaVersion'] == 1,
            'SCHEMA_INVALID')
    key = owner_pins.get(proof['keyId'])
    claims = proof['claims']
    require(type(claims) is dict and set(claims) == CLAIMS, 'SCHEMA_INVALID')
    for field, expression in [('requestId', UUID4), ('actor', ACTOR), ('repoId', REPO),
                              ('previewId', UUID4), ('expectedHead', SHA),
                              ('targetSha', SHA), ('configGeneration', DIGEST),
                              ('fingerprint', DIGEST)]:
        require(_match(claims[field], expression), 'SCHEMA_INVALID')
    for field, expected in [('issuer', 'fortis-gateway'), ('audience', 'fortis-mirror'),
                            ('service', 'mirror'), ('operation', 'execute_windows_pull_v2')]:
        require(type(claims.get(field)) is str and claims[field] == expected,
                'SIGNATURE_INVALID')
    for field in ('requestId', 'actor', 'repoId', 'previewId', 'expectedHead',
                  'targetSha', 'configGeneration'):
        require(claims[field] == dispatch[field], 'SIGNATURE_INVALID')
    require(type(claims['issuedAt']) is int and type(claims['expiresAt']) is int and
            0 <= claims['issuedAt'] < MAX_UNIX and 0 < claims['expiresAt'] <= MAX_UNIX,
            'SCHEMA_INVALID')
    require(claims['issuedAt'] <= now + 5 and now < claims['expiresAt'] <=
            claims['issuedAt'] + 120 and claims['expiresAt'] <= preview_expires,
            'PREVIEW_EXPIRED')
    identity = {field: claims[field] for field in IDENTITY}
    require(hashlib.sha256(_canonical(identity)).hexdigest() == claims['fingerprint'],
            'SIGNATURE_INVALID')
    encoded = proof['signature']
    require(_match(encoded, SIGNATURE), 'SIGNATURE_INVALID')
    try:
        signature = base64.urlsafe_b64decode(encoded + '==')
    except (ValueError, binascii.Error):
        raise ReadError('SIGNATURE_INVALID') from None
    require(len(signature) == 64 and
            base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=') == encoded,
            'SIGNATURE_INVALID')
    _verify_ed25519(key, DOMAIN + _canonical(claims), signature)
    return VerifiedDispatch(route_request_id, authenticated_actor, route_repo,
        dispatch['previewId'], dispatch['expectedHead'], dispatch['targetSha'],
        dispatch['configGeneration'], persisted_preview['origin'],
        persisted_preview['readRequestId'], claims['expiresAt'])
