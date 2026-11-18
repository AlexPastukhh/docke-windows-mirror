"""Offline negative tests for P5 public, owner-review-only generation candidate."""
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.mirror_read_v2_generation import (
    DOMAIN, PublicConfigRejected, candidate, canonical_manifest,
    load_public_manifest, main)


SHA = 'a' * 40
SHA2 = 'b' * 40
CA = 'c' * 64
LEAF = 'd' * 64
REVISION = '76c8fe14-eafd-4674-a84e-a6b69f204c4b'
REVISION2 = '76c8fe14-eafd-4674-a84e-a6b69f204c4c'


def manifest():
    # Synthetic public fixture, NEVER an approved Windows generation.
    return {
        'schemaVersion': 1, 'profile': 'mirror-win-https',
        'sourceSha': SHA, 'readContract': 'mirror-principal-reads-v2.1',
        'mode': 'read-only',
        'tls': {'origin': 'https://host.docker.internal:443',
                'dnsName': 'host.docker.internal',
                'bindIPv4': '127.0.0.1',
                'caCertificateDerSha256': CA,
                'leafCertificateDerSha256': LEAF},
        'scope': {'allowedRepositories': ['fortis-approvals'],
                  'inventories': {'mirror-pilot': ['fortis-approvals']}},
        'ledgerContract': 'existing-browser-core-pull-receipts-v1',
        'readCredentialRevision': REVISION,
    }


class GenerationCandidateTests(unittest.TestCase):
    def test_byte_stable_domain_separated_canonical_generation(self):
        raw = manifest()
        generated = candidate(raw, expected_source_sha=SHA)
        wire = json.dumps(raw, ensure_ascii=True, allow_nan=False,
                          sort_keys=True, separators=(',', ':')).encode('ascii')
        self.assertEqual(generated, sha256(DOMAIN + wire).hexdigest())
        self.assertEqual(len(generated), 64)
        other = dict(reversed(list(raw.items())))
        self.assertEqual(candidate(other, expected_source_sha=SHA), generated)
        self.assertNotEqual(generated, sha256(wire).hexdigest())

    def test_each_trust_change_rotates_candidate(self):
        original = manifest()
        start = candidate(original, expected_source_sha=SHA)
        for name, keys, value, expected in (
            ('source', ('sourceSha',), SHA2, SHA2),
            ('CA', ('tls', 'caCertificateDerSha256'), 'e' * 64, SHA),
            ('leaf', ('tls', 'leafCertificateDerSha256'), 'f' * 64, SHA),
            ('bind', ('tls', 'bindIPv4'), '10.20.30.40', SHA),
            ('credentialRevision', ('readCredentialRevision',), REVISION2, SHA),
        ):
            with self.subTest(name=name):
                modified = deepcopy(original)
                target = modified
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                self.assertNotEqual(candidate(modified, expected_source_sha=expected),
                                    start)

    def test_wrong_source_scope_transport_identity_and_contract_fail_closed(self):
        for name, keys, value in (
            ('wrongSha', ('sourceSha',), SHA2),
            ('blankSha', ('sourceSha',), ''),
            ('extraRepo', ('scope', 'allowedRepositories'),
             ['fortis-approvals', 'other']),
            ('inventoryExpand', ('scope', 'inventories'),
             {'mirror-pilot': ['fortis-approvals','other']}),
            ('inventoryName', ('scope', 'inventories'),
             {'other': ['fortis-approvals']}),
            ('port', ('tls','origin'), 'https://host.docker.internal:8443'),
            ('host', ('tls','dnsName'), 'other.internal'),
            ('readWrite', ('mode',), 'read-write'),
            ('contract', ('readContract',), 'mirror-principal-reads-v2.2'),
            ('ledger', ('ledgerContract',), 'new-ledger'),
            ('badCa', ('tls','caCertificateDerSha256'), '0' * 63),
            ('uppercaseCa', ('tls','caCertificateDerSha256'), 'C' * 64),
            ('wideBind', ('tls','bindIPv4'), '0.0.0.0'),
            ('badBind', ('tls','bindIPv4'), '127.0.0.01'),
            ('ipV6', ('tls','bindIPv4'), '::1'),
            ('badRevision', ('readCredentialRevision',),
             '76c8fe14-eafd-1674-a84e-a6b69f204c4b'),
            ('schema', ('schemaVersion',), 2),
        ):
            with self.subTest(name=name):
                modified = deepcopy(manifest())
                data = modified
                for k in keys[:-1]:
                    data = data[k]
                data[keys[-1]] = value
                with self.assertRaises(PublicConfigRejected):
                    candidate(modified, expected_source_sha=SHA)

    def test_rejects_unknown_fields_wrong_shapes_and_secrets(self):
        for name, mutate in (
            ('topUnknown', lambda x: x.update({'password': 'sensitive'})),
            ('tlsUnknown', lambda x: x['tls'].update({'privateKeyPath': 'C:/secret'})),
            ('topNotDict', lambda x: []),
            ('boolVersion', lambda x: x.update({'schemaVersion': True})),
            ('scopeNotDict', lambda x: x.update({'scope': []})),
            ('duplicateRepo', lambda x: x['scope'].update(
                {'allowedRepositories': ['fortis-approvals', 'fortis-approvals']})),
            ('credentialNotString', lambda x: x.update({'readCredentialRevision': 123})),
        ):
            with self.subTest(name=name):
                obj = manifest()
                changed = mutate(obj)
                if changed is not None:
                    obj = changed
                with self.assertRaises(PublicConfigRejected):
                    candidate(obj, expected_source_sha=SHA)

    def test_manifest_loader_denies_duplicates_nan_oversize_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'public.json'
            p.write_text('{"schemaVersion":1,"schemaVersion":1}')
            with self.assertRaisesRegex(PublicConfigRejected, 'DUPLICATE_MANIFEST_KEY'):
                load_public_manifest(str(p))
            p.write_text('{"status":NaN}')
            with self.assertRaisesRegex(PublicConfigRejected, 'MANIFEST_NONFINITE_NUMBER'):
                load_public_manifest(str(p))
            p.write_bytes(b'a' * 5000)
            with self.assertRaisesRegex(PublicConfigRejected, 'MANIFEST_SIZE_INVALID'):
                load_public_manifest(str(p))
            p.write_bytes(b'[' * 1500 + b']' * 1500)
            with self.assertRaisesRegex(PublicConfigRejected, 'MANIFEST_READ_INVALID'):
                load_public_manifest(str(p))
            p.write_text('{}')
            link = Path(tmp)/'link.json'
            link.symlink_to(p)
            with self.assertRaisesRegex(PublicConfigRejected, 'MANIFEST_LOCATION_INVALID'):
                load_public_manifest(str(link))
            with self.assertRaisesRegex(PublicConfigRejected, 'MANIFEST_LOCATION_INVALID'):
                load_public_manifest('relative.json')

    def test_cli_never_returns_approval_or_leaks_private_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'public.json'
            p.write_text(json.dumps(manifest()))
            out = io.StringIO()
            with patch('sys.stdout', out):
                code = main(['--manifest', str(p), '--expected-source-sha', SHA])
            result = json.loads(out.getvalue())
            self.assertEqual(code, 3)
            self.assertEqual(result['state'], 'OWNER_REVIEW_REQUIRED')
            self.assertIs(result['cutoverAuthorized'], False)
            self.assertEqual(result['candidateMachineGeneration'],
                             candidate(manifest(), expected_source_sha=SHA))
            self.assertNotIn(REVISION, out.getvalue())
            self.assertNotIn('origin', out.getvalue().lower())
            p.write_text('{"secret":"some-private-value"}')
            out = io.StringIO()
            with patch('sys.stdout', out):
                code = main(['--manifest', str(p), '--expected-source-sha', SHA])
            self.assertEqual(code, 2)
            denied = json.loads(out.getvalue())
            self.assertEqual(denied['state'], 'STOP')
            self.assertFalse(denied['cutoverAuthorized'])
            self.assertNotIn('some-private-value', out.getvalue())

    def test_existing_p5_preflight_warning_is_fixed(self):
        root = Path(__file__).parent
        script = root/'tools'/'mirror_read_v2_cutover_preflight.py'
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error', SyntaxWarning)
            compile(script.read_bytes(), str(script), 'exec')


if __name__ == '__main__':
    unittest.main()
