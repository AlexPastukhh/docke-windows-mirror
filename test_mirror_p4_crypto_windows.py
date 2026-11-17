"""P4 real Ed25519/SQLite boundary tests for hosted Windows CI, NEVER live Windows.

Only temporary fake repositories and an ephemeral in-memory test key are used.
The actual Git child is mocked. The shipping listener stays GET-only / POST 405.
A cryptography-free source checkout skips these tests, while Windows CI installs
the pinned verifier package and requires this module to actually run.
"""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mirror_api.core import MirrorCore
from mirror_api.proof_v2 import DOMAIN, IDENTITY, PinnedEd25519Keys, _canonical
from mirror_api.read_v2 import CoreReadV2, ReadError
from mirror_api.write_v2_bridge import PrivateWriteBridge
from mirror_api.write_v2_ingress import WriteIngressProfile
from test_mirror_machine_reads import BoundBackend, CREATE, EFFECT
from test_mirror_atomic_reservation import ACTOR, GEN, REPO, TIME
from test_mirror_write_v2_bridge import READ, WRITE, PATH


@unittest.skipUnless(importlib.util.find_spec('cryptography'),
                     'P4 real cryptography is required in hosted Windows CI')
class HostedWindowsP4CryptoTests(unittest.TestCase):
    def setUp(self):
        # Import lazily: the regular Linux/offline suite remains usable even
        # when this optional Windows CI-only package is not available.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        backend = BoundBackend()
        backend.windows_root = root / 'Repos'
        backend.windows_root.mkdir()
        self.core = MirrorCore(backend, root / 'receipt.sqlite')
        self.addCleanup(self.core.close)
        # Ephemeral test key only: not provisioned, saved, exported or reused
        # as any real Gateway/Windows owner signing credential.
        self.signer = Ed25519PrivateKey.generate()
        public = self.signer.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.pins = PinnedEd25519Keys({'fixture': public}, owner_epoch='e' * 64)
        self.reads = CoreReadV2(self.core, allowed_repos=[REPO],
                                inventories={'mirror-pilot': [REPO]},
                                machine_generation='c' * 64, owner_signing_pins=self.pins)
        self.machine = self.reads._bound_reads()
        self.ticks = [TIME]
        self.machine.clock = lambda: self.ticks[0]
        created = self.reads.call('preview_windows_pull_v2', REPO, {}, principal=ACTOR,
                                  request_id=CREATE, gateway_generation=GEN)
        self.assertIsNone(created['error'])
        self.assertEqual(created['data']['blockers'], [])
        self.preview = self.machine.persisted_preview_for_ask(
            REPO, ACTOR, created['data']['previewId'], GEN)
        self.ingress = WriteIngressProfile(
            read_bearer=READ, write_bearer=WRITE,
            allowed_repos=[REPO], signing_pins=self.pins)
        self.bridge = PrivateWriteBridge(
            ingress=self.ingress, reads=self.reads,
            gateway_generation=GEN, source_enabled=True)
        self.bridge._machine.clock = self.machine.clock
        self.dispatch = self.signed_dispatch()

    def signed_dispatch(self, *, request_id=EFFECT, actor=ACTOR,
                        target_sha=None, signer=None, key_id='fixture'):
        snapshot = self.preview['snapshot']
        fields = dict(requestId=request_id, repoId=REPO, actor=actor,
                      previewId=snapshot['previewId'],
                      expectedHead=snapshot['expectedHead'],
                      targetSha=snapshot['targetSha'] if target_sha is None else target_sha,
                      configGeneration=snapshot['configGeneration'])
        identity = dict(issuer='fortis-gateway', audience='fortis-mirror',
                        service='mirror', operation='execute_windows_pull_v2',
                        **fields)
        claims = {**identity,
                  'fingerprint': hashlib.sha256(_canonical(identity)).hexdigest(),
                  'issuedAt': TIME, 'expiresAt': TIME + 100}
        signature = (self.signer if signer is None else signer).sign(
            DOMAIN + _canonical(claims))
        return {**fields, 'proof': {
            'schemaVersion': 1, 'keyId': key_id, 'claims': claims,
            'signature': base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}}

    def send(self, dispatch=None, *, token=WRITE, bridge=None):
        document = self.dispatch if dispatch is None else dispatch
        raw = json.dumps(document, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True).encode('ascii')
        headers = [('Host', 'host.docker.internal:443'),
                   ('Authorization', 'Bearer ' + token),
                   ('Content-Type', 'application/json'),
                   ('Content-Length', str(len(raw)))]
        return (self.bridge if bridge is None else bridge).process_envelope(
            method='POST', path=PATH, headers=headers, body=raw)

    def counts(self):
        with self.machine.worker._db() as db:
            return (
                db.execute('SELECT COUNT(*) FROM machine_bindings').fetchone()[0],
                db.execute('SELECT COUNT(*) FROM pull_receipts').fetchone()[0])

    def test_real_valid_ed25519_one_effect_then_lookup_after_proof_expiry(self):
        with patch.object(self.machine.worker, '_execute',
                          return_value={'requestId': EFFECT, 'state': 'EXECUTED',
                                        'head': self.dispatch['targetSha']}) as git:
            accepted = self.send()
            self.assertEqual((accepted['workerState'], accepted['effect']),
                             ('EXECUTED', 'CONFIRMED'))
            self.assertEqual(self.counts(), (1, 1))
            self.ticks[0] += 300
            # An old UUID cannot be turned into a new effect, even when the
            # proof is now expired or a tampered signature is provided.
            replay = copy.deepcopy(self.dispatch)
            replay['proof']['signature'] = 'A' * 86
            receipt = self.send(replay)
            self.assertEqual((receipt['workerState'], receipt['effect']),
                             ('EXECUTED', 'CONFIRMED'))
            self.assertEqual(git.call_count, 1)
        self.assertEqual(self.counts(), (1, 1))

    def test_real_invalid_signature_and_wrong_signer_leave_ledger_empty(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        for name, changed in (
            ('bad-signature-byte', self._tamper_signature(self.dispatch)),
            ('different-private-signer', self.signed_dispatch(
                signer=Ed25519PrivateKey.generate())),
        ):
            with self.subTest(name=name), \
                 patch.object(self.machine.worker, '_execute') as git, \
                 self.assertRaisesRegex(ReadError, 'SIGNATURE_INVALID'):
                self.send(changed)
            git.assert_not_called()
            self.assertEqual(self.counts(), (0, 0))

    @staticmethod
    def _tamper_signature(dispatch):
        changed = copy.deepcopy(dispatch)
        sig = bytearray(base64.urlsafe_b64decode(changed['proof']['signature'] + '=='))
        sig[0] ^= 1
        changed['proof']['signature'] = (
            base64.urlsafe_b64encode(sig).decode('ascii').rstrip('='))
        return changed

    def test_real_signed_foreign_actor_or_different_target_is_not_permission(self):
        for label, changed in (
            ('other-actor-with-valid-signature', self.signed_dispatch(actor='repo:foreign')),
            ('other-target-with-valid-signature', self.signed_dispatch(target_sha='f' * 40)),
        ):
            with self.subTest(label=label), \
                 patch.object(self.machine.worker, '_execute') as git, \
                 self.assertRaises(ReadError):
                self.send(changed)
            git.assert_not_called()
            self.assertEqual(self.counts(), (0, 0))

    def test_real_signed_old_preview_cannot_survive_owner_pin_rotation(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        successor = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization
        new_public = successor.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        rotated = PinnedEd25519Keys({'fixture': new_public}, owner_epoch='f' * 64)
        new_reads = CoreReadV2(self.core, allowed_repos=[REPO],
                               inventories={'mirror-pilot': [REPO]},
                               machine_generation='c' * 64,
                               owner_signing_pins=rotated)
        new_bridge = PrivateWriteBridge(
            ingress=WriteIngressProfile(read_bearer=READ, write_bearer=WRITE,
                                        allowed_repos=[REPO], signing_pins=rotated),
            reads=new_reads, gateway_generation=GEN, source_enabled=True)
        new_bridge._machine.clock = self.machine.clock
        with patch.object(self.machine.worker, '_execute') as git, \
             self.assertRaisesRegex(ReadError, 'PREVIEW_STALE'):
            self.send(bridge=new_bridge)
        git.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))

    def test_real_signature_is_not_enough_without_write_bearer_or_fresh_ttl(self):
        with patch.object(self.machine.worker, '_execute') as git, \
             self.assertRaisesRegex(ReadError, 'PERMISSION_DENIED'):
            self.send(token=READ)
        git.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))
        self.ticks[0] = TIME + 121
        with patch.object(self.machine.worker, '_execute') as git, \
             self.assertRaises(ReadError):
            self.send()
        git.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))
