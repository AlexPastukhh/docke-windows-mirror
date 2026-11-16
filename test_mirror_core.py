"""Shared domain/admission regressions; all clones and commands are fixtures."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from mirror_api.browser_pull import BrowserPull
from mirror_api.core import MirrorCore
from mirror_api.operations import ObservationError
from mirror_api.commands import command
from mirror_api.pull import PullDenied, PullWorker
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

    def test_receipt_transaction_commits_and_closes_connection(self):
        worker = self.core.worker()
        with worker._db() as db:
            db.execute("INSERT INTO mirror_store_meta VALUES('fixture','committed')")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')
        with worker._db() as check:
            self.assertEqual(('committed',), check.execute("SELECT value FROM mirror_store_meta WHERE name='fixture'").fetchone())

    def test_receipt_transaction_rolls_back_and_closes_on_exception(self):
        worker = self.core.worker()
        with self.assertRaisesRegex(RuntimeError, 'fixture rollback'):
            with worker._db() as db:
                db.execute("INSERT INTO mirror_store_meta VALUES('fixture','uncommitted')")
                raise RuntimeError('fixture rollback')
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')
        with worker._db() as check:
            self.assertIsNone(check.execute("SELECT value FROM mirror_store_meta WHERE name='fixture'").fetchone())

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


class LegacyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.mirror = FakeMirror()
        self.mirror.windows_root = self.base / 'Repos'
        self.repo = self.mirror.windows_root / 'sample'
        self.repo.mkdir(parents=True)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', ORIGIN)
        self.old = self.commit('old')
        self.target = self.commit('target')
        self.head = self.commit('later')
        self.worker = PullWorker(self.mirror, self.base / 'receipts.sqlite')
        self.addCleanup(self.worker.close)
        self.body = dict(requestId=REQUEST_ID, repoId='sample', expectedHead=self.old,
                         targetSha=self.target, origin=ORIGIN)
        self.saved = dict(requestId=REQUEST_ID, state='UNCERTAIN', head=None,
                          error='WINDOWS_READBACK_UNCONFIRMED', timingsMs={'fastForward':3})
        self.seed()

    def git(self, *args):
        return subprocess.check_output(
            ['git','-c','core.hooksPath=/dev/null','-C',str(self.repo),*args],
            env=self.env, stderr=subprocess.DEVNULL, timeout=10).decode().strip()

    def commit(self, message):
        self.git('commit', '--allow-empty', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def seed(self, **fields):
        receipt = {**self.saved, **fields}
        with self.worker._db() as db:
            db.execute('INSERT OR REPLACE INTO pull_receipts VALUES(?,?,?,?)',
                (REQUEST_ID,json.dumps(self.body,sort_keys=True,separators=(',',':')),
                 receipt['state'],json.dumps(receipt)))

    def test_later_clean_main_confirms_inclusion_without_replaying_or_claiming_target_head(self):
        with mock.patch('mirror_api.operations.command', wraps=command) as calls, \
             mock.patch.object(self.worker, '_execute') as execute:
            result = self.worker.status(REQUEST_ID, repo_id='sample')
            replay = self.worker.run(self.body, repo_id='sample', allowed_repos={'sample'})
            self.assertFalse(self.worker.safety('sample')['blocked'])
        self.assertEqual('EXECUTED', result['state'])
        self.assertEqual('TARGET_INCLUDED_READBACK', result['evidence'])
        self.assertEqual(self.head, result['head'])
        self.assertEqual(self.head, result['observation']['head'])
        self.assertEqual(self.target, result['targetSha'])
        self.assertTrue(result['targetIncluded'])
        self.assertEqual('UNCERTAIN', result['previousState'])
        self.assertEqual(self.saved['error'], result['error'])
        self.assertEqual(self.saved['timingsMs'], result['timingsMs'])
        self.assertEqual(result, replay)
        self.assertIsNone(self.worker.guard.fd, 'Observation never claims a writer')
        execute.assert_not_called()
        for call in calls.call_args_list:
            self.assertFalse({'fetch','merge','reset','push'}.intersection(call.args[0]))

    def test_dirty_wrong_branch_or_origin_does_not_resolve(self):
        (self.repo/'untracked.txt').write_text('fixture')
        self.assertEqual('UNCERTAIN', self.worker.status(REQUEST_ID)['state'])
        (self.repo/'untracked.txt').unlink()
        self.git('checkout', '-b', 'task/other')
        self.assertEqual('UNCERTAIN', self.worker.status(REQUEST_ID)['state'])
        self.git('checkout', 'main')
        self.git('remote', 'set-url', 'origin', 'https://example.invalid/other.git')
        self.assertEqual('UNCERTAIN', self.worker.status(REQUEST_ID)['state'])

    def test_unrelated_or_missing_target_does_not_resolve(self):
        self.git('checkout', '--orphan', 'unrelated')
        self.commit('unrelated')
        self.git('branch', '-M', 'main')
        self.assertEqual('UNCERTAIN', self.worker.status(REQUEST_ID)['state'])
        self.body['targetSha'] = 'f'*40
        self.seed()
        self.assertEqual('UNCERTAIN', self.worker.status(REQUEST_ID)['state'])

    def test_inconsistent_original_ancestry_does_not_resolve(self):
        self.body['expectedHead'] = self.head
        self.seed()
        self.assertEqual('UNCERTAIN', self.worker.status(REQUEST_ID)['state'])

    def test_unknown_errors_dispatched_and_progress_flags_remain_blocked(self):
        for fields in ({'error':'UNKNOWN'}, {'state':'DISPATCHED'},
                       {'phase':'READBACK'}, {'phase':'MERGE'}, {'mergeStarted':True},
                       {'mergeStarted':0}, {'workerFinished':False},
                       {'workerFinished':1}, {'knownFailure':False}):
            with self.subTest(fields=fields):
                self.seed(**fields)
                self.assertEqual(fields.get('state','UNCERTAIN'), self.worker.status(REQUEST_ID)['state'])
                self.assertTrue(self.worker.safety('sample')['blocked'])

    def test_readback_race_or_missing_git_evidence_never_resolves(self):
        from mirror_api.operations import windows_status
        observed = windows_status(self.mirror.windows_root,'sample',ORIGIN)
        for after in ({**observed,'head':self.target}, {**observed,'clean':False},
                      {**observed,'originValid':False}, {**observed,'error':'UNAVAILABLE'}):
            with self.subTest(after=after), mock.patch('mirror_api.pull.windows_status',
                    side_effect=[observed,after]):
                self.assertEqual('UNCERTAIN', self.worker.status(REQUEST_ID)['state'])
        with mock.patch('mirror_api.pull.relation_to_sha', return_value='unknown'):
            self.assertEqual('UNCERTAIN', self.worker.status(REQUEST_ID)['state'])
