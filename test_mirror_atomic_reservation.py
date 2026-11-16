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
        api = CoreReadV2(self.core, allowed_repos=[REPO],
                         inventories={'mirror-pilot': [REPO]}, machine_generation='c' * 64)
        self.machine = api._bound_reads()
        self.ticks = [TIME]
        self.machine.clock = lambda: self.ticks[0]
        created = api.call('preview_windows_pull_v2', REPO, {}, principal=ACTOR,
                           request_id=CREATE, gateway_generation=GEN)
        self.assertEqual(created['data']['blockers'], [])
        self.preview = self.machine.persisted_preview_for_ask(
            REPO, ACTOR, created['data']['previewId'], GEN)
        self.pins = PinnedEd25519Keys({'fixture': b'\x07' * 32})
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

    def test_atomic_reservation_binds_same_receipt_without_git(self):
        with patch.object(self.machine.worker, 'run') as run, \
             patch.object(self.machine.worker.guard, 'acquire') as guard, \
             patch.object(self.core, 'preview_observation') as observe:
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
            observe.assert_not_called()
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

    def test_read_uuid_cannot_be_reused_as_effect(self):
        with self.assertRaisesRegex(ReadError, 'REQUEST_ID_CONFLICT'):
            self.reserve(self._dispatch(CREATE), request_id=CREATE)
        self.assertEqual(self.counts(), (0, 0))
