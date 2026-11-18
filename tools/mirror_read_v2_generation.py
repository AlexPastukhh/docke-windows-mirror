"""Candidate-only public READ v2 machine_generation; NO owner approval or activation.

CLI accepts one small, closed, PUBLIC JSON manifest outside the source tree.
Produces a deterministic candidate SHA-256, never calls a listener, reads
credentials, changes the SQLite ledger, or authorizes a release. Owner must
independently review the factual inputs, bind them to live Windows/Gateway
evidence, and expressly approve the digest before it can be used.
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sys
import uuid


DOMAIN = b'fortis-mirror/p5/read-v2/machine-generation/v1\x00'
SHA40 = re.compile(r'[0-9a-f]{40}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
MAX_MANIFEST_BYTES = 4096
UPSTREAM = 'https://host.docker.internal:443'
CANONICAL_SCOPE = {
    'allowedRepositories': ['fortis-approvals'],
    'inventories': {'mirror-pilot': ['fortis-approvals']},
}


class PublicConfigRejected(ValueError):
    """Fixed error codes only; never propagate a manifest's private values."""


def require(condition, code='PUBLIC_CONFIG_INVALID'):
    if not condition:
        raise PublicConfigRejected(code)


def closed(record, fields):
    require(type(record) is dict and set(record) == set(fields))


def _revision(value):
    require(type(value) is str, 'READ_CREDENTIAL_REVISION_INVALID')
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise PublicConfigRejected('READ_CREDENTIAL_REVISION_INVALID') from None
    require(parsed.version == 4 and str(parsed) == value,
            'READ_CREDENTIAL_REVISION_INVALID')


def canonical_manifest(raw, *, expected_source_sha):
    """Only exact, public, versioned fields are admitted; no secrets/paths."""
    require(type(expected_source_sha) is str and SHA40.fullmatch(expected_source_sha),
            'EXPECTED_SOURCE_SHA_INVALID')
    closed(raw, ('schemaVersion', 'profile', 'sourceSha', 'readContract',
                 'mode', 'tls', 'scope', 'ledgerContract', 'readCredentialRevision'))
    require(type(raw['schemaVersion']) is int and raw['schemaVersion'] == 1,
            'GENERATION_SCHEMA_UNKNOWN')
    require(raw['profile'] == 'mirror-win-https')
    require(raw['mode'] == 'read-only')
    require(raw['readContract'] == 'mirror-principal-reads-v2.1')
    require(raw['ledgerContract'] == 'existing-browser-core-pull-receipts-v1')
    require(type(raw['sourceSha']) is str and
            SHA40.fullmatch(raw['sourceSha']) and raw['sourceSha'] == expected_source_sha,
            'SOURCE_SHA_MISMATCH')

    tls = raw['tls']
    closed(tls, ('origin', 'dnsName', 'bindIPv4', 'caCertificateDerSha256',
                 'leafCertificateDerSha256'))
    require(tls['origin'] == UPSTREAM and tls['dnsName'] == 'host.docker.internal',
            'TLS_IDENTITY_INVALID')
    for name in ('caCertificateDerSha256', 'leafCertificateDerSha256'):
        require(type(tls[name]) is str and SHA256.fullmatch(tls[name]),
                'PUBLIC_TLS_FINGERPRINT_INVALID')
    require(type(tls['bindIPv4']) is str, 'TLS_BIND_INVALID')
    try:
        address = ipaddress.IPv4Address(tls['bindIPv4'])
    except ipaddress.AddressValueError:
        raise PublicConfigRejected('TLS_BIND_INVALID') from None
    require(tls['bindIPv4'] == str(address) and
            not (address.is_unspecified or address.is_multicast or
                 address.is_reserved or address.is_link_local) and
            (not address.is_loopback or tls['bindIPv4'] == '127.0.0.1'),
            'TLS_BIND_INVALID')

    scope = raw['scope']
    closed(scope, ('allowedRepositories', 'inventories'))
    require(type(scope['allowedRepositories']) is list and
            scope['allowedRepositories'] == CANONICAL_SCOPE['allowedRepositories'] and
            type(scope['inventories']) is dict and
            set(scope['inventories']) == {'mirror-pilot'} and
            type(scope['inventories']['mirror-pilot']) is list and
            scope['inventories']['mirror-pilot'] ==
            CANONICAL_SCOPE['inventories']['mirror-pilot'],
            'GENERATION_SCOPE_INVALID')
    _revision(raw['readCredentialRevision'])
    return json.dumps(raw, sort_keys=True, ensure_ascii=True,
                      allow_nan=False, separators=(',', ':')).encode('ascii')


def candidate(raw, *, expected_source_sha):
    canonical = canonical_manifest(raw, expected_source_sha=expected_source_sha)
    return hashlib.sha256(DOMAIN + canonical).hexdigest()


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_MANIFEST_KEY')
        result[key] = value
    return result


def load_public_manifest(path):
    require(type(path) is str, 'MANIFEST_LOCATION_INVALID')
    p = Path(path)
    require(p.is_absolute() and p.is_file() and not p.is_symlink() and
            not p.resolve().is_relative_to(Path(__file__).resolve().parent.parent),
            'MANIFEST_LOCATION_INVALID')
    try:
        size = p.stat().st_size
        require(0 < size <= MAX_MANIFEST_BYTES, 'MANIFEST_SIZE_INVALID')
        data = p.read_bytes()
        require(len(data) == size, 'MANIFEST_SIZE_INVALID')
        return json.loads(
            data.decode('utf-8'), object_pairs_hook=_unique_keys,
            parse_constant=lambda value: require(False, 'MANIFEST_NONFINITE_NUMBER'))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise PublicConfigRejected('MANIFEST_READ_INVALID') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--expected-source-sha', required=True)
    args = parser.parse_args(argv)
    try:
        value = candidate(load_public_manifest(args.manifest),
                          expected_source_sha=args.expected_source_sha)
        result = {'schemaVersion': 1, 'state': 'OWNER_REVIEW_REQUIRED',
                  'cutoverAuthorized': False, 'candidateMachineGeneration': value,
                  'source': 'PUBLIC_MANIFEST_CANDIDATE_ONLY',
                  'ownerGates': ['SOURCE_AND_LIVE_CERT_FINGERPRINT_REVIEW',
                                 'CREDENTIAL_REVISION_OWNER_REVIEW',
                                 'LEDGER_AND_ROLLBACK_EVIDENCE',
                                 'WINDOWS_AND_GATEWAY_READ_V2_LIVE_ACCEPTANCE',
                                 'EXPLICIT_OWNER_GO']}
        code = 3  # Even a valid manifest is never owner approval.
    except (PublicConfigRejected, ValueError, TypeError, RecursionError):
        result = {'schemaVersion': 1, 'state': 'STOP',
                  'cutoverAuthorized': False, 'code': 'GENERATION_MANIFEST_DENIED'}
        code = 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(',', ':')))
    return code


if __name__ == '__main__':
    sys.exit(main())
