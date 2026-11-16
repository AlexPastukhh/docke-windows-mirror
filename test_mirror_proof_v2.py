"""Offline-only P4 proof verification; never opens a WRITE route or executes Git."""
import base64
from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mirror_api.core import MirrorCore
from mirror_api.read_v2 import CoreReadV2, ReadError
from mirror_api.proof_v2 import (DOMAIN, PinnedEd25519Keys, VerifiedDispatch,
                                  verify_new_dispatch)
from test_mirror_machine_reads import BoundBackend, CREATE, EFFECT, ORIGIN

ACTOR = 'repo:fixture'
REPO = 'fortis-approvals'
GEN = 'd' * 64
KEY = 'owner-verified-key'
PUBLIC = b'\x09' * 32
NOW = 1794877200


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode('ascii')


class ProofV2Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        backend = BoundBackend(); backend.windows_root = root/'Repos'
        backend.windows_root.mkdir()
        self.core = MirrorCore(backend, root/'receipts.sqlite')
        self.addCleanup(self.core.close)
        api = CoreReadV2(self.core, allowed_repos=[REPO],
                         inventories={'mirror-pilot': [REPO]}, machine_generation='c'*64)
        self.machine = api._bound_reads()
        self.machine.clock = lambda: NOW
        value = api.call('preview_windows_pull_v2', REPO, {}, principal=ACTOR,
                         request_id=CREATE, gateway_generation=GEN)
        self.assertIsNone(value['error'])
        self.assertEqual(value['data']['blockers'], [])
        self.preview = self.machine.persisted_preview_for_ask(
            REPO, ACTOR, value['data']['previewId'], GEN)
        self.pinset = PinnedEd25519Keys({KEY: PUBLIC})
        self.dispatch = self._dispatch()

    def _dispatch(self):
        value = self.preview['snapshot']
        identity = dict(issuer='fortis-gateway', audience='fortis-mirror',
                        service='mirror', operation='execute_windows_pull_v2',
                        requestId=EFFECT, actor=ACTOR, repoId=REPO,
                        previewId=value['previewId'], expectedHead=value['expectedHead'],
                        targetSha=value['targetSha'], configGeneration=value['configGeneration'])
        claims = {**identity,
                  'fingerprint': hashlib.sha256(canonical(identity)).hexdigest(),
                  'issuedAt': NOW, 'expiresAt': NOW+100}
        return {'requestId': EFFECT, 'repoId': REPO, 'actor': ACTOR,
                'previewId': value['previewId'], 'expectedHead': value['expectedHead'],
                'targetSha': value['targetSha'], 'configGeneration': value['configGeneration'],
                'proof': {'schemaVersion': 1, 'keyId': KEY, 'claims': claims,
                          'signature': base64.urlsafe_b64encode(bytes(64)).decode('ascii').rstrip('=')}}

    def verify(self, dispatch=None, **changes):
        options=dict(authenticated_actor=ACTOR, route_repo=REPO, route_request_id=EFFECT,
                     persisted_preview=self.preview, owner_pins=self.pinset, now=NOW)
        options.update(changes)
        return verify_new_dispatch(self.dispatch if dispatch is None else dispatch, **options)

    def test_exact_frozen_preview_and_domain_check_with_no_worker_or_git(self):
        with patch('mirror_api.proof_v2._verify_ed25519') as crypto, \
             patch.object(self.core, 'preview_observation') as observe, \
             patch.object(self.machine.worker, 'run') as run, \
             patch.object(self.machine.worker, 'status') as status, \
             patch.object(self.machine.worker.guard, 'acquire') as writer:
            result = self.verify()
            self.assertIsInstance(result, VerifiedDispatch)
            self.assertEqual((result.request_id,result.actor,result.repo_id,result.origin,
                              result.read_request_id,result.expires_at),
                             (EFFECT,ACTOR,REPO,ORIGIN,CREATE,NOW+100))
            crypto.assert_called_once_with(PUBLIC, DOMAIN + canonical(self.dispatch['proof']['claims']),
                                           bytes(64))
            with self.assertRaises(FrozenInstanceError): result.actor='other'
            observe.assert_not_called();run.assert_not_called()
            status.assert_not_called();writer.assert_not_called()

    def test_untrusted_route_actor_repo_request_id_and_payload_denied(self):
        for changed in ({'actor':'other'}, {'repoId':'other'},
                        {'requestId':CREATE}, {'actor':'a space'}, {'extra':'x'}):
            with self.subTest(changed=changed),self.assertRaises(ReadError):
                self.verify({**self.dispatch, **changed})
        for param,value in [('authenticated_actor','other'), ('route_repo','other'),
                            ('route_request_id',CREATE)]:
            with self.subTest(param=param),self.assertRaises(ReadError):
                self.verify(**{param:value})

    def test_payload_and_claim_symmetric_binding_and_fingerprint(self):
        for field, value in [('expectedHead','f'*40), ('targetSha','f'*40),
                             ('previewId',CREATE), ('configGeneration','f'*64)]:
            with self.subTest(field=field),self.assertRaises(ReadError):
                self.verify({**self.dispatch,field:value})
        for field, value in [('actor','other'), ('requestId',CREATE),
                             ('repoId','other'), ('expectedHead','f'*40),
                             ('fingerprint','f'*64),('issuer','caller'),
                             ('audience','wrong'),('operation','unrelated')]:
            changed={**self.dispatch,'proof':{**self.dispatch['proof'],
                     'claims':{**self.dispatch['proof']['claims'],field:value}}}
            with self.subTest(field=field),self.assertRaises(ReadError):self.verify(changed)
        changed={**self.dispatch,'proof':{**self.dispatch['proof'],
                 'claims':{**self.dispatch['proof']['claims'],'approved':True}}}
        with self.assertRaises(ReadError):self.verify(changed)

    def test_pins_and_signature_fail_closed(self):
        with self.assertRaisesRegex(ReadError,'UNKNOWN_KEY'):
            self.verify(owner_pins=PinnedEd25519Keys({}))
        for pins in ({'bad key!':PUBLIC},{KEY:b'bad'}, {KEY:'a'*32}):
            with self.subTest(pins=pins),self.assertRaises(ReadError):PinnedEd25519Keys(pins)
        for sig in ('bad', 'A'*85, 'A'*87, 'A'*84+'==', '!'*86, 'A'*85+'B'):
            proof={**self.dispatch['proof'],'signature':sig}
            with self.subTest(sig=sig),self.assertRaises(ReadError):
                self.verify({**self.dispatch,'proof':proof})
        with patch('mirror_api.proof_v2._verify_ed25519', side_effect=ReadError('SIGNATURE_INVALID')):
            with self.assertRaisesRegex(ReadError,'SIGNATURE_INVALID'):self.verify()
        with self.assertRaisesRegex(ReadError,'PROOF_REQUIRED'):
            self.verify({**self.dispatch,'proof':None})

    def test_expiration_clock_skew_and_generation_stale(self):
        proof=self.dispatch['proof']; claims=proof['claims']
        for issued,expires in [(NOW+6,NOW+100),(NOW,NOW),
                               (NOW,NOW+121),(True,NOW+100),
                               (NOW,False),(NOW-121,NOW+1)]:
            changed={**self.dispatch,'proof':{**proof,'claims':{**claims,
                       'issuedAt':issued,'expiresAt':expires}}}
            with self.subTest(issued=issued,expires=expires), self.assertRaises(ReadError):
                self.verify(changed)
        for now in (NOW+120,True,-1):
            with self.subTest(now=now),self.assertRaises(ReadError):self.verify(now=now)
        stale={**self.preview,'snapshot':{**self.preview['snapshot'],
               'configGeneration':'a'*64}}
        with self.assertRaisesRegex(ReadError,'PREVIEW_STALE'):
            self.verify(persisted_preview=stale)

    def test_missing_cryptography_is_fail_closed_and_never_trusts_bearer_alone(self):
        if importlib.util.find_spec('cryptography') is None:
            with self.assertRaisesRegex(ReadError,'SOURCE_UNAVAILABLE'):
                self.verify()
        else:
            with self.assertRaisesRegex(ReadError,'SIGNATURE_INVALID'):
                self.verify()  # well-shaped but deliberately unsigned fixture

    def test_key_inventory_is_owned_snapshot_not_mutable_remote_discovery(self):
        original={KEY: PUBLIC}
        trusted=PinnedEd25519Keys(original)
        original.clear()
        with patch('mirror_api.proof_v2._verify_ed25519') as crypto:
            self.verify(owner_pins=trusted)
            crypto.assert_called_once()
        with self.assertRaisesRegex(ReadError,'UNKNOWN_KEY'):
            self.verify(owner_pins=original)

    def test_no_proof_path_can_reserve_or_return_legacy_browser_uuid(self):
        with self.machine.worker._db() as db:
            before=(db.execute('SELECT COUNT(*) FROM machine_bindings').fetchone()[0],
                    db.execute('SELECT COUNT(*) FROM pull_receipts').fetchone()[0])
        with patch('mirror_api.proof_v2._verify_ed25519'):
            self.verify()
        with self.machine.worker._db() as db:
            after=(db.execute('SELECT COUNT(*) FROM machine_bindings').fetchone()[0],
                   db.execute('SELECT COUNT(*) FROM pull_receipts').fetchone()[0])
        self.assertEqual(before,(0,0))
        self.assertEqual(before,after)
        with self.assertRaisesRegex(ReadError,'PERMISSION_DENIED'):
            self.machine.lookup(REPO,ACTOR,EFFECT)

    @unittest.skipUnless(importlib.util.find_spec('cryptography'),
                         'Windows verifier crypto dependency not available in Repo Docker')
    def test_real_signature_then_tamper_rejected(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key=Ed25519PrivateKey.from_private_bytes(b'\x05'*32)
        from cryptography.hazmat.primitives import serialization
        public=key.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                            format=serialization.PublicFormat.Raw)
        signed=key.sign(DOMAIN + canonical(self.dispatch['proof']['claims']))
        proof={**self.dispatch['proof'],'signature':base64.urlsafe_b64encode(signed).decode('ascii').rstrip('=')}
        dispatch={**self.dispatch,'proof':proof}
        result=self.verify(dispatch,owner_pins=PinnedEd25519Keys({KEY:public}))
        self.assertEqual(result.target_sha,self.preview['snapshot']['targetSha'])
        bad=bytearray(signed);bad[0]^=1
        proof_bad={**proof,'signature':base64.urlsafe_b64encode(bad).decode('ascii').rstrip('=')}
        with self.assertRaisesRegex(ReadError,'SIGNATURE_INVALID'):
            self.verify({**dispatch,'proof':proof_bad},owner_pins=PinnedEd25519Keys({KEY:public}))
