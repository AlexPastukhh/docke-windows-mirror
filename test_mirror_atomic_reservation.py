"""P4 offline SQLite atomic reservation; no network, Git or live WRITE route."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from mirror_api.core import MirrorCore
from mirror_api.pull import PullDenied, PullWorker
from mirror_api.operations import ObservationError
from mirror_api.proof_v2 import PinnedEd25519Keys, IDENTITY, _canonical
from mirror_api.read_v2 import CoreReadV2, ReadError
from test_mirror_machine_reads import BoundBackend, CREATE, EFFECT, ORIGIN
from test_mirror_proof_v2 import ProofV2Tests

REPO = 'fortis-approvals'
ACTOR = 'repo:fixture'
GEN = 'd' * 64
TIME = 1794877200
BROWSER = '5166d435-e811-41d3-a8c8-c7d807942c25'
SECOND = '6166d435-e811-41d3-a8c8-c7d807942c25'


class AtomicReservationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        backend = BoundBackend()
        backend.windows_root = root / 'Repos'
        backend.windows_root.mkdir()
        self.core = MirrorCore(backend, root / 'receipts.sqlite')
        self.addCleanup(self.core.close)
        self.pins = PinnedEd25519Keys({'fixture': b'\x07' * 32}, owner_epoch='e' * 64)
        api = CoreReadV2(self.core, allowed_repos=[REPO],
                         inventories={'mirror-pilot': [REPO]}, machine_generation='c' * 64,
                         owner_signing_pins=self.pins)
        self.machine = api._bound_reads()
        self.ticks = [TIME]
        self.machine.clock = lambda: self.ticks[0]
        created = api.call('preview_windows_pull_v2', REPO, {}, principal=ACTOR,
                           request_id=CREATE, gateway_generation=GEN)
        self.assertEqual(created['data']['blockers'], [])
        self.preview = self.machine.persisted_preview_for_ask(
            REPO, ACTOR, created['data']['previewId'], GEN)
        self.dispatch = self._dispatch()

    def _dispatch(self, request_id=EFFECT):
        old = ProofV2Tests._dispatch(self)
        changed = {**old, 'requestId': request_id}
        changed['proof'] = {**old['proof'],
                            'claims': {**old['proof']['claims'], 'requestId': request_id}}
        identity = {key: changed['proof']['claims'][key] for key in IDENTITY}
        changed['proof']['claims']['fingerprint'] = hashlib.sha256(_canonical(identity)).hexdigest()
        changed['proof']['keyId'] = 'fixture'
        return changed

    def reserve(self, dispatch=None, actor=ACTOR, repo=REPO, request_id=EFFECT,
                gateway_generation=GEN):
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier:
            result = self.machine.reserve_signed_candidate(
                dispatch=self.dispatch if dispatch is None else dispatch, actor=actor,
                repo=repo, request_id=request_id, gateway_generation=gateway_generation,
                owner_pins=self.pins)
            return result, verifier.call_count

    def counts(self):
        with self.machine.worker._db() as db:
            return (db.execute('SELECT COUNT(*) FROM machine_bindings').fetchone()[0],
                    db.execute('SELECT COUNT(*) FROM pull_receipts').fetchone()[0])

    def test_owner_pin_rotation_invalidates_frozen_preview_but_keeps_receipt(self):
        self.reserve()
        original_generation = self.machine.generation
        self.assertEqual(self.counts(), (1, 1))
        for label, pins in (
                ('changed-public-key', PinnedEd25519Keys(
                    {'fixture': b'\x08' * 32}, owner_epoch='e' * 64)),
                ('changed-owner-epoch', PinnedEd25519Keys(
                    {'fixture': b'\x07' * 32}, owner_epoch='f' * 64)),
                ('all-keys-revoked', PinnedEd25519Keys({}, owner_epoch='f' * 64))):
            with self.subTest(label=label):
                api = CoreReadV2(self.core, allowed_repos=[REPO],
                                 inventories={'mirror-pilot': [REPO]},
                                 machine_generation='c' * 64, owner_signing_pins=pins)
                rotated = api._bound_reads()
                rotated.clock = self.machine.clock
                self.assertNotEqual(rotated.generation, original_generation)
                with self.assertRaisesRegex(ReadError, 'PREVIEW_STALE'):
                    rotated.persisted_preview_for_ask(
                        REPO, ACTOR, self.dispatch['previewId'], GEN)
                # Post-rotation receipt readback never needs the old signer,
                # its private material or an unexpired preview.
                self.assertEqual(rotated.lookup(REPO, ACTOR, EFFECT)['effect'], 'UNKNOWN')
                self.assertEqual(self.counts(), (1, 1))

    def test_owner_pin_profile_is_required_and_caller_cannot_replace_it(self):
        copied = PinnedEd25519Keys({'fixture': b'\x07' * 32}, owner_epoch='e' * 64)
        self.assertEqual(copied.revision, self.pins.revision)
        with patch('mirror_api.proof_v2._verify_ed25519') as crypto, \
             self.assertRaisesRegex(ReadError, 'UNKNOWN_KEY'):
            self.machine.reserve_signed_candidate(
                dispatch=self.dispatch, actor=ACTOR, repo=REPO,
                request_id=EFFECT, gateway_generation=GEN, owner_pins=copied)
        crypto.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))
        with self.assertRaisesRegex(ReadError, 'UNKNOWN_KEY'):
            CoreReadV2(self.core, allowed_repos=[REPO],
                       inventories={'mirror-pilot': [REPO]}, machine_generation='c' * 64,
                       owner_signing_pins=PinnedEd25519Keys({'fixture': b'\x07' * 32}))
        with self.assertRaisesRegex(ReadError, 'UNKNOWN_KEY'):
            CoreReadV2(self.core, allowed_repos=[REPO],
                       inventories={'mirror-pilot': [REPO]}, machine_generation=None,
                       owner_signing_pins=self.pins)
        # READ v2 with no owner profile remains available and cannot admit WRITE.
        read_only = CoreReadV2(self.core, allowed_repos=[REPO],
                               inventories={'mirror-pilot': [REPO]},
                               machine_generation='c' * 64)._bound_reads()
        with patch('mirror_api.proof_v2._verify_ed25519') as crypto, \
             self.assertRaisesRegex(ReadError, 'UNKNOWN_KEY'):
            read_only.reserve_signed_candidate(
                dispatch=self.dispatch, actor=ACTOR, repo=REPO,
                request_id=EFFECT, gateway_generation=GEN, owner_pins=self.pins)
        crypto.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))

    def test_owner_profile_epoch_and_exact_key_set_bind_generation(self):
        one = PinnedEd25519Keys({'key-b': b'\x01' * 32,
                                 'key-a': b'\x02' * 32}, owner_epoch='0' * 64)
        two = PinnedEd25519Keys({'key-a': b'\x02' * 32,
                                 'key-b': b'\x01' * 32}, owner_epoch='0' * 64)
        self.assertEqual(one.revision, two.revision)
        self.assertNotEqual(one.revision, PinnedEd25519Keys(
            {'key-a': b'\x02' * 32, 'key-b': b'\x01' * 32},
            owner_epoch='1' * 64).revision)
        self.assertNotEqual(one.revision, PinnedEd25519Keys(
            {'key-a': b'\x02' * 32}, owner_epoch='0' * 64).revision)
        for invalid in ('not-hex', 'G' * 64, True, 1, ''):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ReadError,'UNKNOWN_KEY'):
                PinnedEd25519Keys({'fixture': b'\x07' * 32}, owner_epoch=invalid)
        old = self.pins
        self.assertEqual(old.get('fixture'), b'\x07' * 32)
        with self.assertRaisesRegex(ReadError, 'UNKNOWN_KEY'):
            old.get('unknown')
        self.assertEqual(self.counts(), (0, 0))

    def test_atomic_reservation_binds_same_receipt_without_git(self):
        with patch.object(self.machine.worker, 'run') as run, \
             patch.object(self.machine.worker.guard, 'acquire') as guard, \
             patch.object(self.core, 'preview_observation',
                          wraps=self.core.preview_observation) as observe:
            saved, calls = self.reserve()
            self.assertEqual(saved, {'requestId': EFFECT, 'reservation': 'RESERVED',
                                     'dispatchReady': False})
            self.assertEqual(calls, 1)
            self.assertEqual(self.counts(), (1, 1))
            read = self.machine.lookup(REPO, ACTOR, EFFECT)
            self.assertEqual(read['workerState'], 'DISPATCHED')
            self.assertEqual(read['effect'], 'UNKNOWN')
            run.assert_not_called()
            guard.assert_not_called()
            observe.assert_called_once_with(REPO)
        with self.machine.worker._db() as db:
            binding = db.execute('SELECT actor,repo_id,identity FROM machine_bindings WHERE request_id=?',
                                 (EFFECT,)).fetchone()
            receipt = db.execute('SELECT payload,state FROM pull_receipts WHERE request_id=?',
                                 (EFFECT,)).fetchone()
        self.assertEqual(binding[:2], (ACTOR, REPO))
        self.assertEqual(json.loads(binding[2]), {k: self.dispatch[k] for k in
            ('previewId', 'expectedHead', 'targetSha', 'configGeneration')})
        self.assertEqual(receipt[1], 'DISPATCHED')
        self.assertEqual(json.loads(receipt[0]), dict(requestId=EFFECT, repoId=REPO,
            expectedHead=self.dispatch['expectedHead'], targetSha=self.dispatch['targetSha'],
            origin=ORIGIN))

    def test_expired_replay_is_lookup_only_new_uuid_is_denied(self):
        self.reserve()
        self.ticks[0] += 300
        replay, crypto = self.reserve()
        self.assertEqual((replay['reservation'], crypto), ('EXISTING', 0))
        self.assertEqual(self.counts(), (1, 1))
        with self.assertRaisesRegex(ReadError, 'PREVIEW_EXPIRED'):
            self.reserve(self._dispatch(SECOND), request_id=SECOND)
        self.assertEqual(self.counts(), (1, 1))

    def test_foreign_and_conflicting_uuid_and_browser_receipt_cannot_be_adopted(self):
        self.reserve()
        for changes in ({'targetSha': 'f' * 40}, {'expectedHead': 'f' * 40},
                        {'configGeneration': '0' * 64}, {'previewId': CREATE}):
            with self.subTest(changes=changes), self.assertRaises(ReadError):
                self.reserve({**self.dispatch, **changes})
        with self.assertRaisesRegex(ReadError, 'PERMISSION_DENIED'):
            self.reserve(actor='someone-else')
        browser = dict(requestId=BROWSER, repoId=REPO, expectedHead='b' * 40,
                       targetSha='a' * 40, origin=ORIGIN)
        with self.machine.worker._db() as db:
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (BROWSER, json.dumps(browser), 'EXECUTED',
                        json.dumps({'requestId': BROWSER, 'state': 'EXECUTED'})))
        with self.assertRaisesRegex(ReadError, 'PERMISSION_DENIED'):
            self.reserve(self._dispatch(BROWSER), request_id=BROWSER)
        self.assertEqual(self.counts(), (1, 2))

    def test_browser_cannot_adopt_exact_machine_receipt_even_after_execution(self):
        # Before this gate, a byte-for-byte matching Browser payload could
        # read a machine receipt via PullWorker.run's existing-UUID shortcut.
        self.reserve()
        with self.machine.worker._db() as db:
            payload = json.loads(db.execute(
                'SELECT payload FROM pull_receipts WHERE request_id=?',
                (EFFECT,)).fetchone()[0])
        with patch.object(self.machine.worker,'_execute') as git, \
             patch.object(self.machine.worker,'status') as status:
            with self.assertRaisesRegex(PullDenied,'MACHINE_REQUEST_ID_PRIVATE'):
                self.machine.worker.run(payload,repo_id=REPO,allowed_repos={REPO})
            git.assert_not_called()
            status.assert_not_called()
        with self.machine.worker._db() as db:
            db.execute('UPDATE pull_receipts SET state=? WHERE request_id=?',
                       ('EXECUTED',EFFECT))
        with patch.object(self.machine.worker,'status') as status:
            with self.assertRaisesRegex(PullDenied,'MACHINE_REQUEST_ID_PRIVATE'):
                self.machine.worker.run(payload,repo_id=REPO,allowed_repos={REPO})
            status.assert_not_called()

    def test_browser_cannot_take_machine_read_uuid_without_existing_receipt(self):
        # READ UUID exists before any machine effect reservation. Browser may
        # not insert its own receipt under that identity.
        browser=dict(requestId=CREATE,repoId=REPO,expectedHead='b'*40,
                     targetSha='a'*40,origin=ORIGIN)
        with patch.object(self.machine.worker,'_execute') as git, \
             self.assertRaisesRegex(PullDenied,'MACHINE_REQUEST_ID_PRIVATE'):
            self.machine.worker.run(browser,repo_id=REPO,allowed_repos={REPO})
        git.assert_not_called()
        self.assertEqual(self.counts(),(0,0))

    def test_browser_after_machine_reservation_blocked_before_git(self):
        self.reserve()
        browser = dict(requestId=BROWSER, repoId=REPO, expectedHead='b' * 40,
                       targetSha='a' * 40, origin=ORIGIN)
        with patch.object(self.machine.worker.guard, 'acquire') as guard, \
             patch.object(self.machine.worker, '_execute') as effect:
            result = self.machine.worker.run(browser, repo_id=REPO, allowed_repos=[REPO])
            self.assertEqual(result['state'], 'FAILED')
            self.assertEqual(result['error'], 'REPOSITORY_OPERATION_PENDING')
            guard.assert_not_called()
            effect.assert_not_called()
        self.assertEqual(self.counts(), (1, 2))

    def test_pending_browser_reservation_blocks_machine(self):
        browser = dict(requestId=BROWSER, repoId=REPO, expectedHead='b' * 40,
                       targetSha='a' * 40, origin=ORIGIN)
        with self.machine.worker._db() as db:
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (BROWSER, json.dumps(browser), 'UNCERTAIN',
                        json.dumps({'requestId': BROWSER, 'state': 'UNCERTAIN'})))
        with self.assertRaisesRegex(ReadError, 'REPOSITORY_OPERATION_PENDING'):
            self.reserve()
        self.assertEqual(self.counts(), (0, 1))

    def test_trigger_failure_rolls_back_both_inserts(self):
        with self.machine.worker._db() as db:
            db.execute("""CREATE TRIGGER refuse_machine_receipt BEFORE INSERT ON pull_receipts
                          WHEN NEW.request_id='2166d435-e811-41d3-a8c8-c7d807942c25'
                          BEGIN SELECT RAISE(ABORT,'atomic rollback fixture'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'atomic rollback fixture'):
            self.reserve()
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_proof_unknown_key_and_failed_verification_no_rows(self):
        with self.assertRaisesRegex(ReadError, 'PROOF_REQUIRED'):
            self.reserve({**self.dispatch, 'proof': None})
        with patch('mirror_api.proof_v2._verify_ed25519',
                   side_effect=ReadError('SIGNATURE_INVALID')):
            with self.assertRaisesRegex(ReadError, 'SIGNATURE_INVALID'):
                self.machine.reserve_signed_candidate(
                    dispatch=self.dispatch, actor=ACTOR, repo=REPO, request_id=EFFECT,
                    gateway_generation=GEN, owner_pins=self.pins)
        self.pins = PinnedEd25519Keys({})
        with self.assertRaisesRegex(ReadError, 'UNKNOWN_KEY'):
            self.reserve()
        self.assertEqual(self.counts(), (0, 0))

    def test_db_change_between_signature_and_commit_fails(self):
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier:
            def change(*_):
                with self.machine.worker._db() as db:
                    db.execute('UPDATE machine_previews SET origin=? WHERE preview_id=?',
                               ('changed-origin', self.dispatch['previewId']))
            verifier.side_effect = change
            with self.assertRaisesRegex(ReadError, 'PREVIEW_STALE'):
                self.machine.reserve_signed_candidate(
                    dispatch=self.dispatch, actor=ACTOR, repo=REPO, request_id=EFFECT,
                    gateway_generation=GEN, owner_pins=self.pins)
        self.assertEqual(self.counts(), (0, 0))

    def test_changed_fresh_source_semantics_denied_before_reservation(self):
        # BoundBackend normalizes its stored fixture on each read; mutate the
        # actual fresh response, not the cached fixture before normalization.
        original = self.core.backend.inventory
        cases = [
            ('windows-head', lambda b: b['windows'].update(head='f' * 40)),
            ('windows-clean', lambda b: b['windows'].update(clean=False)),
            ('windows-branch', lambda b: b['windows'].update(branch='task/unmerged')),
            ('windows-origin', lambda b: b['windows'].update(origin='wrong-origin')),
            ('windows-relation', lambda b: b['windows'].update(relation='diverged')),
            ('docker-head', lambda b: b['docker']['primary'].update(head='f' * 40)),
            ('docker-clean', lambda b: b['docker']['primary'].update(clean=False)),
            ('docker-origin', lambda b: b['docker']['primary'].update(origin='wrong-origin')),
            ('github-repo', lambda b: b['github'].update(githubRepository='other')),
            ('github-head', lambda b: b['github'].update(head='f' * 40)),
        ]
        for name, mutate in cases:
            def changed(*args, **kwargs):
                result = original(*args, **kwargs)
                mutate(result['repositories'][0])
                return result
            with self.subTest(name=name), \
                 patch.object(self.core.backend, 'inventory', side_effect=changed), \
                 self.assertRaisesRegex(ReadError, 'PREVIEW_STALE'):
                self.reserve()
            self.assertEqual(self.counts(), (0, 0))

    def test_observation_timestamp_drift_is_not_fingerprint_change(self):
        original = self.core.backend.inventory
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            result['repositories'][0]['windows']['checkedAt'] = '2026-11-17T01:00:30+00:00'
            result['checkedAt'] = '2026-11-17T01:00:30+00:00'
            return result
        with patch.object(self.core.backend, 'inventory', side_effect=changed), \
             patch.object(self.core, 'preview_observation',
                          wraps=self.core.preview_observation) as source:
            reserved, signature_count = self.reserve()
            self.assertEqual(reserved['reservation'], 'RESERVED')
            self.assertEqual(signature_count, 1)
            source.assert_called_once_with(REPO)
        self.assertEqual(self.counts(), (1, 1))

    def test_fresh_source_exception_does_not_reserve(self):
        from mirror_api.operations import ObservationError
        with patch.object(self.core, 'preview_observation',
                          side_effect=ObservationError('fixture unavailable')):
            with self.assertRaisesRegex(ReadError, 'SOURCE_UNAVAILABLE'):
                self.reserve()
        self.assertEqual(self.counts(), (0, 0))

    def test_parallel_same_uuid_is_one_reservation_and_one_lookup(self):
        # Two callers can pass proof verification before either reserves. The
        # second SQLite BEGIN IMMEDIATE must still be status-only, never error
        # into a second effect/association or silently replace a receipt.
        barrier = threading.Barrier(2)
        def verified(*_args):
            barrier.wait(timeout=8)
        with patch('mirror_api.proof_v2._verify_ed25519', side_effect=verified):
            def call():
                return self.machine.reserve_signed_candidate(
                    dispatch=self.dispatch, actor=ACTOR, repo=REPO, request_id=EFFECT,
                    gateway_generation=GEN, owner_pins=self.pins)
            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(call)
                f2 = pool.submit(call)
                states = sorted((f1.result(timeout=15)['reservation'],
                                 f2.result(timeout=15)['reservation']))
        self.assertEqual(states, ['EXISTING', 'RESERVED'])
        self.assertEqual(self.counts(), (1, 1))

    def test_separate_machine_uuid_blocked_while_reservation_unresolved(self):
        self.reserve()
        with self.assertRaisesRegex(ReadError, 'REPOSITORY_OPERATION_PENDING'):
            self.reserve(self._dispatch(SECOND), request_id=SECOND)
        self.assertEqual(self.counts(), (1, 1))

    def dispatch_effect(self):
        with patch('mirror_api.proof_v2._verify_ed25519') as verify:
            result = self.machine.dispatch_signed_candidate(
                dispatch=self.dispatch, actor=ACTOR, repo=REPO,
                request_id=EFFECT, gateway_generation=GEN, owner_pins=self.pins)
            return result, verify.call_count

    def test_machine_handoff_reuses_one_worker_and_claims_before_effect(self):
        seen = []
        def execute(value, *, timings):
            self.assertEqual(value['requestId'], EFFECT)
            with self.machine.worker._db() as db:
                result = json.loads(db.execute('SELECT result FROM pull_receipts WHERE request_id=?',
                                               (EFFECT,)).fetchone()[0])
                seen.append(result['phase'])
                self.assertEqual('MACHINE_CLAIMED', result['phase'])
                self.assertFalse(result['mergeStarted'])
            return {'requestId':EFFECT,'state':'EXECUTED',
                    'head':self.dispatch['targetSha']}
        with patch.object(self.machine.worker,'_execute',side_effect=execute) as git, \
             patch.object(self.machine.worker, 'run') as browser:
            result, crypto = self.dispatch_effect()
            self.assertEqual((result['workerState'], result['effect']), ('EXECUTED','CONFIRMED'))
            self.assertEqual(crypto,1)
            self.assertEqual(seen,['MACHINE_CLAIMED'])
            self.assertEqual(git.call_count,1)
            browser.assert_not_called()
            self.assertEqual(self.counts(),(1,1))
            self.ticks[0] += 400
            result2, crypto2 = self.dispatch_effect()
            self.assertEqual((result2['workerState'],result2['effect'],crypto2),
                             ('EXECUTED','CONFIRMED',0))
            self.assertEqual(git.call_count,1)

    def test_machine_handoff_crash_before_claim_is_never_reattached(self):
        # Reservation committed, but the process crashed before consuming its
        # ephemeral permit. Restart must not resurrect the source-only effect.
        with patch.object(self.machine.worker,'_run_machine_reserved',
                          side_effect=KeyboardInterrupt), \
             self.assertRaises(KeyboardInterrupt):
            self.dispatch_effect()
        with self.machine.worker._db() as db:
            phase = json.loads(db.execute('SELECT result FROM pull_receipts').fetchone()[0])['phase']
            self.assertEqual(phase,'RESERVED_MACHINE')
        self.assertEqual(self.machine.worker._machine_handoff_permits,{})
        self.assertEqual(self.machine.lookup(REPO,ACTOR,EFFECT)['effect'],'UNKNOWN')
        with patch.object(self.machine.worker,'_execute') as effect:
            existing, verifier = self.dispatch_effect()
            self.assertEqual((existing['workerState'],existing['effect'],verifier),
                             ('DISPATCHED','UNKNOWN',0))
            effect.assert_not_called()
        # A distinct worker that opens the same durable SQLite ledger has NO
        # permit, so guessing the old UUID and identity cannot start Git.
        second = PullWorker(self.core.backend,self.machine.worker.path)
        self.addCleanup(second.close)
        identity = {k:self.dispatch[k] for k in
                    ('previewId','expectedHead','targetSha','configGeneration')}
        with patch.object(second,'_execute') as effect, \
             self.assertRaisesRegex(PullDenied,'MACHINE_HANDOFF_NOT_FRESH'):
            second._run_machine_reserved(request_id=EFFECT,repo_id=REPO,
                actor=ACTOR,identity=identity,now=self.ticks[0],permit=object())
        effect.assert_not_called()

    def test_crash_after_durable_claim_never_replays_git(self):
        with patch.object(self.machine.worker,'_execute',
                          side_effect=KeyboardInterrupt) as effect, \
             self.assertRaises(KeyboardInterrupt):
            self.dispatch_effect()
        with self.machine.worker._db() as db:
            row = db.execute('SELECT state,result FROM pull_receipts').fetchone()
            self.assertEqual(row[0],'DISPATCHED')
            self.assertEqual(json.loads(row[1])['phase'],'MACHINE_CLAIMED')
        self.assertEqual(effect.call_count,1)
        with patch.object(self.machine.worker,'_execute') as repeated:
            status, signature = self.dispatch_effect()
            self.assertEqual((status['workerState'],status['effect'],signature),
                             ('DISPATCHED','UNKNOWN',0))
            repeated.assert_not_called()

    def test_lost_ack_and_observation_failure_keep_unknown_without_retry(self):
        with patch.object(self.machine.worker,'_execute',
                          side_effect=ObservationError('WINDOWS_READBACK_UNCONFIRMED')) as effect, \
             patch('mirror_api.pull.windows_status',
                   side_effect=ObservationError('WINDOWS_READ_UNAVAILABLE')):
            first, verify = self.dispatch_effect()
        self.assertEqual(verify,1)
        self.assertEqual(first['effect'],'UNKNOWN')
        self.assertEqual(effect.call_count,1)
        with patch.object(self.machine.worker,'_execute') as no_retry:
            repeated, signature = self.dispatch_effect()
        self.assertEqual((repeated['effect'],signature),('UNKNOWN',0))
        no_retry.assert_not_called()

    def test_machine_guard_unavailable_is_known_pre_effect_failure(self):
        with patch.object(self.machine.worker.guard,'acquire',
                          side_effect=OSError('MIRROR_WRITER_BUSY')), \
             patch.object(self.machine.worker,'_execute') as effect:
            status, crypto = self.dispatch_effect()
            self.assertEqual(crypto,1)
            self.assertEqual((status['workerState'],status['effect']),('FAILED','NONE'))
            self.assertEqual(status['errorCode'],'WRITER_BUSY')
            effect.assert_not_called()
        self.assertEqual(self.counts(),(1,1))

    def test_machine_uses_existing_worker_live_preconditions_before_git(self):
        with patch('mirror_api.pull.docker_inventory',return_value={}) as inventory, \
             patch('mirror_api.pull.command') as git:
            status, crypto = self.dispatch_effect()
        self.assertEqual(crypto,1)
        self.assertEqual(status['effect'],'UNKNOWN')
        inventory.assert_called_once_with(REPO)
        git.assert_not_called()
        with patch('mirror_api.proof_v2._verify_ed25519') as verifier, \
             patch.object(self.machine.worker,'_execute') as repeat:
            status2 = self.machine.dispatch_signed_candidate(
                dispatch=self.dispatch, actor=ACTOR, repo=REPO, request_id=EFFECT,
                gateway_generation=GEN, owner_pins=self.pins)
            self.assertEqual(status2['effect'],'UNKNOWN')
            verifier.assert_not_called()
            repeat.assert_not_called()

    def test_permit_expires_at_proof_ttl_even_before_preview_ttl(self):
        permit = object()
        with patch('mirror_api.proof_v2._verify_ed25519'):
            row = self.machine.reserve_signed_candidate(
                dispatch=self.dispatch,actor=ACTOR,repo=REPO,request_id=EFFECT,
                gateway_generation=GEN,owner_pins=self.pins,_handoff_permit=permit)
        self.assertEqual(row['reservation'],'RESERVED')
        self.ticks[0] += 105  # proof expires at TIME+100; preview at TIME+120
        identity = {k:self.dispatch[k] for k in
                    ('previewId','expectedHead','targetSha','configGeneration')}
        with patch.object(self.machine.worker,'_execute') as git, \
             self.assertRaisesRegex(PullDenied,'MACHINE_PROOF_EXPIRED'):
            self.machine.worker._run_machine_reserved(
                request_id=EFFECT,repo_id=REPO,actor=ACTOR,
                identity=identity,now=self.ticks[0],permit=permit)
        git.assert_not_called()
        with self.assertRaisesRegex(PullDenied,'MACHINE_HANDOFF_NOT_FRESH'):
            self.machine.worker._run_machine_reserved(
                request_id=EFFECT,repo_id=REPO,actor=ACTOR,
                identity=identity,now=self.ticks[0],permit=permit)
        self.assertEqual(self.counts(),(1,1))
        self.assertEqual(self.machine.lookup(REPO,ACTOR,EFFECT)['effect'],'UNKNOWN')

    def test_tampered_durable_actor_binding_refuses_one_time_handoff(self):
        permit = object()
        with patch('mirror_api.proof_v2._verify_ed25519'):
            self.machine.reserve_signed_candidate(
                dispatch=self.dispatch,actor=ACTOR,repo=REPO,request_id=EFFECT,
                gateway_generation=GEN,owner_pins=self.pins,_handoff_permit=permit)
        with self.machine.worker._db() as db:
            db.execute('UPDATE machine_bindings SET actor=? WHERE request_id=?',
                       ('foreign',EFFECT))
        identity = {k:self.dispatch[k] for k in
                    ('previewId','expectedHead','targetSha','configGeneration')}
        with patch.object(self.machine.worker,'_execute') as git, \
             self.assertRaisesRegex(PullDenied,'MACHINE_RESERVATION_MISMATCH'):
            self.machine.worker._run_machine_reserved(
                request_id=EFFECT,repo_id=REPO,actor=ACTOR,identity=identity,
                now=self.ticks[0],permit=permit)
        git.assert_not_called()
        with self.assertRaisesRegex(PullDenied,'MACHINE_HANDOFF_NOT_FRESH'):
            self.machine.worker._run_machine_reserved(
                request_id=EFFECT,repo_id=REPO,actor=ACTOR,identity=identity,
                now=self.ticks[0],permit=permit)
        with self.machine.worker._db() as db:
            self.assertEqual('RESERVED_MACHINE',json.loads(
                db.execute('SELECT result FROM pull_receipts').fetchone()[0])['phase'])

    def test_browser_refused_during_machine_effect_and_never_executes_git(self):
        entered = threading.Event()
        release = threading.Event()
        attempts = []
        def execute(value, *, timings):
            entered.set()
            if not release.wait(timeout=8):
                raise AssertionError('fixture timed out')
            attempts.append(value['requestId'])
            return {'requestId':EFFECT,'state':'EXECUTED','head':self.dispatch['targetSha']}
        browser=dict(requestId=BROWSER,repoId=REPO,expectedHead='b'*40,
                     targetSha='a'*40,origin=ORIGIN)
        with patch('mirror_api.proof_v2._verify_ed25519'), \
             patch.object(self.machine.worker,'_execute',side_effect=execute) as effect:
            with ThreadPoolExecutor(max_workers=2) as pool:
                candidate=pool.submit(self.machine.dispatch_signed_candidate,
                    dispatch=self.dispatch,actor=ACTOR,repo=REPO,request_id=EFFECT,
                    gateway_generation=GEN,owner_pins=self.pins)
                try:
                    self.assertTrue(entered.wait(timeout=8))
                    denied=self.machine.worker.run(browser,repo_id=REPO,allowed_repos=[REPO])
                    self.assertEqual((denied['state'],denied['error']),
                                     ('FAILED','REPOSITORY_OPERATION_PENDING'))
                finally:
                    release.set()
                result=candidate.result(timeout=12)
            self.assertEqual((result['workerState'],result['effect']),('EXECUTED','CONFIRMED'))
            self.assertEqual(attempts,[EFFECT])
            self.assertEqual(effect.call_count,1)
        self.assertEqual(self.counts(),(1,2))

    def test_read_uuid_cannot_be_reused_as_effect(self):
        with self.assertRaisesRegex(ReadError, 'REQUEST_ID_CONFLICT'):
            self.reserve(self._dispatch(CREATE), request_id=CREATE)
        self.assertEqual(self.counts(), (0, 0))
