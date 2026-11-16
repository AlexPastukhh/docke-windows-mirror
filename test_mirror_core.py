"""Shared domain/admission regressions; all clones and commands are fixtures."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from mirror_api.browser_pull import BrowserPull
from mirror_api.core import MirrorCore
from mirror_api.operations import ObservationError
from mirror_api.pull import PullDenied
from test_mirror_browser_pull import FakeMirror, OLD, NEW, ORIGIN, REQUEST_ID


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.backend = FakeMirror()
        self.backend.windows_root = self.base / 'Repos'
        self.backend.windows_root.mkdir()
        self.core = MirrorCore(self.backend, self.base / 'one.sqlite')
        self.addCleanup(self.core.close)
        self.body = {'requestId':REQUEST_ID, 'repoId':'sample', 'expectedHead':OLD,
                     'targetSha':NEW, 'origin':ORIGIN}

    def other(self):
        core = MirrorCore(self.backend, self.base / 'different.sqlite')
        self.addCleanup(core.close)
        return core

    def test_adapters_use_exactly_one_worker_and_guard(self):
        first = BrowserPull(self.backend, core=self.core)
        second = BrowserPull(self.backend, core=self.core)
        self.assertIs(first._worker(), second._worker())
        self.assertIs(self.core._guard, first._worker().guard)
        self.assertIs(first.core, second.core)

    def test_browser_lookup_bindings_are_bounded_before_another_snapshot(self):
        adapter=BrowserPull(self.backend,core=self.core,clock=lambda:100)
        for index in range(adapter.MAX_REQUESTS):
            adapter.requests[str(index)]={'session':'owner','repoId':'sample','time':100,'state':'DISPATCHING'}
        with mock.patch.object(self.core,'preview') as snapshot:
            with self.assertRaisesRegex(PullDenied,'PULL_RECEIPT_LOOKUP_LIMIT_REACHED'):
                adapter.preview('sample','owner')
        snapshot.assert_not_called()

    def test_preview_does_not_acquire_a_writer_or_resolve_receipts(self):
        worker = self.core.worker()
        result = {'requestId':REQUEST_ID,'state':'UNCERTAIN','head':None,'error':'GITHUB_MAIN_CHANGED'}
        with worker._db() as db:
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (REQUEST_ID,json.dumps(self.body),'UNCERTAIN',json.dumps(result)))
        with mock.patch.object(self.core._guard, 'acquire') as acquire, \
             mock.patch.object(worker, 'status') as lookup:
            preview = self.core.preview('sample')
        self.assertFalse(preview['available'])
        self.assertIn('PREVIOUS_PULL_UNRESOLVED',preview['reasons'])
        acquire.assert_not_called()
        lookup.assert_not_called()
        with worker._db() as db:
            self.assertEqual('UNCERTAIN',db.execute('SELECT state FROM pull_receipts').fetchone()[0])

    def test_different_database_and_adapter_cannot_create_second_writer(self):
        self.core.worker().guard.acquire()
        other = self.other()
        adapter = BrowserPull(self.backend, core=other)
        preview = adapter.preview('sample','owner')
        self.assertTrue(preview['available'],'Preview is an observation, not a writer lease')
        with mock.patch.object(other.worker(), '_execute') as effect:
            refused = adapter.execute('sample','owner',preview['previewId'],'PULL_WINDOWS_MAIN')
            effect.assert_not_called()
        self.assertEqual(('FAILED','MIRROR_WRITER_BUSY','PRE_EFFECT_REFUSAL'),
                         (refused['state'],refused['error'],refused['evidence']))
        self.assertEqual('FAILED',adapter.status('sample','owner',preview['requestId'])['state'])
        with self.assertRaisesRegex(PullDenied,'PULL_RECEIPT_NOT_OWNED'):
            adapter.status('sample','other-session',preview['requestId'])

    def test_closed_controller_does_not_allow_replaying_an_admission_failure(self):
        self.core.worker().guard.acquire()
        other = self.other()
        refused = other.execute(self.body,'sample')
        self.assertEqual('FAILED',refused['state'])
        self.core.close()
        with mock.patch.object(other.worker(),'_execute',return_value={
                'requestId':REQUEST_ID,'state':'EXECUTED','head':NEW}) as effect:
            self.assertEqual('FAILED',other.execute(self.body,'sample')['state'])
            effect.assert_not_called()
            fresh = {**self.body,'requestId':'22345678-1234-4234-8234-123456789abc'}
            rejected = other.execute(fresh,'sample')
            self.assertEqual(('FAILED','MIRROR_RECEIPT_STORE_MISMATCH'),(rejected['state'],rejected['error']))
            effect.assert_not_called()
        correct = MirrorCore(self.backend,self.base/'one.sqlite')
        self.addCleanup(correct.close)
        with mock.patch.object(correct.worker(),'_execute',return_value={
                'requestId':fresh['requestId'],'state':'EXECUTED','head':NEW}) as effect:
            self.assertEqual('EXECUTED',correct.execute(fresh,'sample')['state'])
            effect.assert_called_once()

    def test_recreated_empty_database_cannot_replace_the_bound_ledger(self):
        self.core.worker().guard.acquire()
        original_id=self.core.worker().store_id
        self.core.close()
        (self.base/'one.sqlite').unlink()
        replaced=MirrorCore(self.backend,self.base/'one.sqlite');self.addCleanup(replaced.close)
        self.assertNotEqual(original_id,replaced.worker().store_id)
        with mock.patch.object(replaced.worker(),'_execute') as effect:
            result=replaced.execute(self.body,'sample')
        self.assertEqual(('FAILED','MIRROR_RECEIPT_STORE_MISMATCH'),(result['state'],result['error']))
        effect.assert_not_called()

    def test_read_only_core_cannot_initialize_writer_or_hidden_fetch(self):
        core = MirrorCore(self.backend,self.base/'readonly.sqlite',read_only=True)
        self.addCleanup(core.close)
        with self.assertRaises(PullDenied):core.worker()
        with self.assertRaises(PullDenied):core.preview('sample')
        with self.assertRaises(PullDenied):core.execute(self.body,'sample')
        with self.assertRaises(ObservationError):core.inventory(fetch_windows=True)
        self.assertFalse((self.base/'readonly.sqlite').exists())
        self.assertFalse((self.backend.windows_root/'.fortis-mirror-writer.lock').exists())

    def test_same_clone_effects_are_serialized(self):
        entered, release = threading.Event(), threading.Event()
        original = self.backend.check_repository
        def check(repo):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(repo)
        with mock.patch.object(self.backend,'check_repository',side_effect=check), \
             ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.core.check_repository,'sample')
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaisesRegex(PullDenied,'REPOSITORY_OPERATION_PENDING'):
                    self.core.check_repository('sample')
            finally:release.set()
            first.result(timeout=3)

    def test_busy_clone_refuses_fresh_pull_without_waiting_or_losing_uuid(self):
        entered,release=threading.Event(),threading.Event()
        original=self.backend.check_repository
        def check(repo):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(repo)
        with mock.patch.object(self.backend,'check_repository',side_effect=check), \
             mock.patch.object(self.core.worker(),'_execute') as effect, ThreadPoolExecutor(2) as pool:
            first=pool.submit(self.core.check_repository,'sample')
            self.assertTrue(entered.wait(2))
            try:
                result=pool.submit(self.core.execute,self.body,'sample').result(timeout=1)
                self.assertEqual(('FAILED','REPOSITORY_OPERATION_PENDING'),(result['state'],result['error']))
                self.assertEqual('FAILED',self.core.worker().status(REQUEST_ID)['state'])
                effect.assert_not_called()
            finally:release.set()
            first.result(timeout=3)

    def test_independent_clones_can_check_in_parallel_in_one_core(self):
        barrier = threading.Barrier(2)
        def check(repo):
            barrier.wait(timeout=3)
            data = self.backend.inventory(remote=True,repo=repo)
            data['repositories'][0]['repoId'] = repo
            return data
        with mock.patch.object(self.backend,'check_repository',side_effect=check), \
             ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.core.check_repository,x) for x in ('sample','other')]
            self.assertEqual(['sample','other'],[f.result(timeout=4)['repositories'][0]['repoId'] for f in futures])

    def test_core_check_uses_same_writer_guard_as_pull(self):
        self.core.check_repository('sample')
        other = self.other()
        with self.assertRaisesRegex(PullDenied,'MIRROR_WRITER_BUSY'):
            other.check_repository('sample')
        self.assertEqual('FAILED',other.execute(self.body,'sample')['state'])

    def test_unconfirmed_child_cannot_be_resolved_from_transient_target_head(self):
        worker = self.core.worker()
        result = {'requestId':REQUEST_ID,'state':'UNCERTAIN','head':None,'error':'PULL_EXECUTION_UNCONFIRMED',
                  'phase':'MERGE','mergeStarted':True,'workerFinished':False,'knownFailure':False}
        with worker._db() as db:
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (REQUEST_ID,json.dumps(self.body),'UNCERTAIN',json.dumps(result)))
        with mock.patch('mirror_api.pull.windows_status',return_value={
                'head':NEW,'branch':'main','clean':True,'originValid':True,'error':None}):
            self.assertEqual('UNCERTAIN',worker.status(REQUEST_ID)['state'])
