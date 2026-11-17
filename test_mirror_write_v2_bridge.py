"""Offline P4 WRITE chain composition. No new HTTP handler or Windows Git effect."""
import json
import unittest
from unittest.mock import patch

from mirror_api.gateway_readonly import ReadOnlyHandler
from mirror_api.gateway_v2 import ReadV2Handler, route
from mirror_api.proof_v2 import PinnedEd25519Keys
from mirror_api.read_v2 import CoreReadV2, ReadError
from mirror_api.write_v2_ingress import WriteIngressProfile
from mirror_api.write_v2_bridge import PrivateWriteBridge
import test_mirror_atomic_reservation as atomic

ACTOR, EFFECT, GEN, REPO = atomic.ACTOR, atomic.EFFECT, atomic.GEN, atomic.REPO

READ = 'R' * 44
WRITE = 'W' * 44
PATH = f'/gateway/v2/repositories/{REPO}/pull'


class PrivateWriteBridgeTests(unittest.TestCase):
    def _dispatch(self, request_id=EFFECT):
        return atomic.AtomicReservationTests._dispatch(self, request_id)

    def setUp(self):
        # Reuse exact registered-actor preview and shared PullWorker ledger
        # fixtures, but inherit NONE of AtomicReservationTests test methods.
        atomic.AtomicReservationTests.setUp(self)
        self.reads = CoreReadV2(
            self.core, allowed_repos=[REPO], inventories={'mirror-pilot':[REPO]},
            machine_generation='c' * 64, owner_signing_pins=self.pins)
        self.ingress = WriteIngressProfile(
            read_bearer=READ, write_bearer=WRITE,
            allowed_repos=[REPO], signing_pins=self.pins)
        self.bridge = PrivateWriteBridge(
            ingress=self.ingress, reads=self.reads,
            gateway_generation=GEN, source_enabled=True)
        self.bridge._machine.clock = self.machine.clock  # same deterministic fixture clock
        self.body = self.encode(self.dispatch)

    @staticmethod
    def encode(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=True,
                          separators=(',', ':')).encode('ascii')

    def headers(self, body=None, *, token=WRITE):
        payload = self.body if body is None else body
        return [('Host', 'host.docker.internal:443'),
                ('Authorization', 'Bearer '+token),
                ('Content-Type','application/json'),
                ('Content-Length',str(len(payload)))]

    def run_bridge(self, *, body=None, headers=None, method='POST', path=PATH):
        payload=self.body if body is None else body
        hdrs=self.headers(payload) if headers is None else headers
        return self.bridge.process_envelope(
            method=method,path=path,headers=hdrs,body=payload)

    def test_proof_pinned_preview_atomically_claims_once_without_real_git(self):
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute',
                          return_value={'requestId':EFFECT,'state':'EXECUTED',
                                        'head':self.dispatch['targetSha']}) as effect, \
             patch.object(self.machine.worker,'run') as browser:
            result=self.run_bridge()
            self.assertEqual((result['workerState'],result['effect']),
                             ('EXECUTED','CONFIRMED'))
            self.assertEqual(verifier.call_count,1)
            self.assertEqual(effect.call_count,1)
            browser.assert_not_called()
            self.assertEqual(self.machine.lookup(REPO,ACTOR,EFFECT)['effect'],'CONFIRMED')
            replay=self.run_bridge()
            self.assertEqual(replay['effect'],'CONFIRMED')
            self.assertEqual(verifier.call_count,1)
            self.assertEqual(effect.call_count,1)
        with self.machine.worker._db() as db:
            bindings=db.execute('SELECT COUNT(*) FROM machine_bindings').fetchone()[0]
            receipts=db.execute('SELECT COUNT(*) FROM pull_receipts').fetchone()[0]
        self.assertEqual((bindings,receipts),(1,1))

    def test_source_disabled_by_default_and_no_post_handler(self):
        dormant=PrivateWriteBridge(ingress=self.ingress,reads=self.reads,
                                   gateway_generation=GEN)
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute') as effect, \
             self.assertRaisesRegex(ReadError,'POLICY_DENIED'):
            dormant.process_envelope(method='POST',path=PATH,
                                    headers=self.headers(),body=self.body)
        verifier.assert_not_called()
        effect.assert_not_called()
        self.assertIs(ReadV2Handler.do_POST,ReadOnlyHandler.deny_write)
        with self.assertRaises(ReadError):
            route(PATH)
        self.assertFalse(hasattr(self.bridge,'serve_forever'))
        self.assertNotIn(WRITE,repr(self.bridge))
        self.assertNotIn(READ,repr(self.bridge))

    def test_untrusted_actor_cannot_find_other_principal_preview(self):
        altered={**self.dispatch,'actor':'repo:foreign',
                 'proof':{**self.dispatch['proof'],
                          'claims':{**self.dispatch['proof']['claims'],
                                    'actor':'repo:foreign'}}}
        raw=self.encode(altered)
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute') as effect, \
             self.assertRaises(ReadError):
            self.run_bridge(body=raw)
        verifier.assert_not_called()
        effect.assert_not_called()
        with self.machine.worker._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM machine_bindings').fetchone()[0],0)

    def test_wrong_write_bearer_denied_before_proof_or_lookup(self):
        for token in (READ,'x'*44,WRITE+'x'):
            with self.subTest(token=token[:2]), \
                 patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
                 patch.object(self.machine.worker,'_execute') as effect, \
                 self.assertRaisesRegex(ReadError,'PERMISSION_DENIED'):
                self.run_bridge(headers=self.headers(token=token))
            verifier.assert_not_called()
            effect.assert_not_called()

    def test_unsigned_or_mutated_proof_never_dispatches(self):
        with patch('mirror_api.proof_v2._verify_ed25519',
                   side_effect=ReadError('SIGNATURE_INVALID')) as verifier, \
             patch.object(self.machine.worker,'_execute') as effect, \
             self.assertRaisesRegex(ReadError,'SIGNATURE_INVALID'):
            self.run_bridge()
        self.assertEqual(verifier.call_count,1)
        effect.assert_not_called()
        with self.machine.worker._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pull_receipts').fetchone()[0],0)

    def test_future_handler_cannot_supply_another_key_profile_or_scope(self):
        cloned=PinnedEd25519Keys({'fixture': b'\x07'*32},owner_epoch='e'*64)
        self.assertEqual(cloned.revision,self.pins.revision)
        wrong_ingress=WriteIngressProfile(
            read_bearer=READ,write_bearer=WRITE,
            allowed_repos=[REPO],signing_pins=cloned)
        with self.assertRaisesRegex(ReadError,'POLICY_DENIED'):
            PrivateWriteBridge(ingress=wrong_ingress,reads=self.reads,
                               gateway_generation=GEN,source_enabled=True)
        narrow=CoreReadV2(self.core,allowed_repos=['other'],inventories={
            'other-scope':['other']},machine_generation='c'*64,
            owner_signing_pins=self.pins)
        with self.assertRaisesRegex(ReadError,'POLICY_DENIED'):
            PrivateWriteBridge(ingress=self.ingress,reads=narrow,
                               gateway_generation=GEN,source_enabled=True)
        for bad in (True,None,'g'*64,'a'*63):
            with self.subTest(bad=bad),self.assertRaisesRegex(ReadError,'POLICY_DENIED'):
                PrivateWriteBridge(ingress=self.ingress,reads=self.reads,
                                   gateway_generation=bad,source_enabled=True)

    def test_expired_proof_does_not_reserve_new_effect(self):
        self.ticks[0] += 130
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute') as effect, \
             self.assertRaises(ReadError):
            self.run_bridge()
        verifier.assert_not_called()
        effect.assert_not_called()
        with self.machine.worker._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pull_receipts').fetchone()[0],0)

    def test_crash_before_guard_one_request_stays_readback_only(self):
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute',
                          side_effect=KeyboardInterrupt) as git:
            with self.assertRaises(KeyboardInterrupt):
                self.run_bridge()
            self.assertEqual(git.call_count,1)
            # Replay is status-only despite lost response/unknown Git effect.
            result=self.run_bridge()
            self.assertEqual((result['workerState'],result['effect']),('DISPATCHED','UNKNOWN'))
            self.assertEqual(git.call_count,1)
            self.assertEqual(verifier.call_count,1)

    def test_existing_uuid_with_expired_proof_is_readback_only(self):
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute',
                          return_value={'requestId':EFFECT,'state':'EXECUTED',
                                        'head':self.dispatch['targetSha']}) as git:
            self.assertEqual(self.run_bridge()['effect'],'CONFIRMED')
            self.ticks[0] += 600
            replay={**self.dispatch,'proof':{**self.dispatch['proof'],
                      'signature':'B'*86}}
            self.assertEqual(self.run_bridge(body=self.encode(replay))['effect'],
                             'CONFIRMED')
            self.assertEqual(git.call_count,1)
            self.assertEqual(verifier.call_count,1)

    def test_gateway_generation_rotation_cannot_admit_an_old_preview(self):
        rotated=PrivateWriteBridge(ingress=self.ingress,reads=self.reads,
                                   gateway_generation='f'*64,source_enabled=True)
        rotated._machine.clock=self.machine.clock
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute') as git, \
             self.assertRaisesRegex(ReadError,'PREVIEW_STALE'):
            rotated.process_envelope(method='POST',path=PATH,
                                     headers=self.headers(),body=self.body)
        verifier.assert_not_called()
        git.assert_not_called()
        with self.machine.worker._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM machine_bindings').fetchone()[0],0)

    def test_no_owner_pin_profile_means_no_bridge_activation(self):
        read_only=CoreReadV2(self.core, allowed_repos=[REPO],
                             inventories={'mirror-pilot':[REPO]},
                             machine_generation='c'*64)
        with self.assertRaisesRegex(ReadError,'POLICY_DENIED'):
            PrivateWriteBridge(ingress=self.ingress,reads=read_only,
                               gateway_generation=GEN,source_enabled=True)

    def test_malformed_http_json_denied_before_proof_and_worker(self):
        dup=self.body.replace(b'"actor":"repo:fixture"',
                              b'"actor":"repo:fixture","actor":"forged"',1)
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute') as git, \
             self.assertRaisesRegex(ReadError,'SCHEMA_INVALID'):
            self.run_bridge(body=dup)
        verifier.assert_not_called()
        git.assert_not_called()

    def test_wrong_scope_or_http_method_denies_before_signature(self):
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute') as effect:
            for bad_method,bad_path in (('GET',PATH),('POST',PATH+'/'),
                                         ('POST',PATH.replace(REPO,'other'))):
                with self.subTest(method=bad_method,path=bad_path),self.assertRaises(ReadError):
                    self.run_bridge(method=bad_method,path=bad_path)
            verifier.assert_not_called()
            effect.assert_not_called()
