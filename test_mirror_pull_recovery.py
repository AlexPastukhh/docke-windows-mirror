"""Regression fixtures for failed fetch, durable recovery and session boundaries."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from mirror_api.browser_pull import BrowserPull
from mirror_api.git_errors import fetch_failure
from mirror_api.pull import PullDenied, PullWorker
from test_mirror_browser_pull import FakeMirror, OLD, NEW, ORIGIN, REQUEST_ID


class PullRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mirror = FakeMirror()
        self.mirror.windows_root = Path(self.tmp.name) / 'Repos'
        self.path = Path(self.tmp.name) / 'receipts.sqlite'
        self.worker = PullWorker(self.mirror, self.path)
        self.body = {'requestId':REQUEST_ID, 'repoId':'sample', 'expectedHead':OLD,
                     'targetSha':NEW, 'origin':ORIGIN}
        self.observed = {'error':None, 'head':OLD, 'branch':'main', 'clean':True, 'originValid':True}

    def seed(self, state='UNCERTAIN', **fields):
        result = {'requestId':REQUEST_ID, 'state':state, 'head':None, **fields}
        with self.worker._db() as db:
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (REQUEST_ID, json.dumps(self.body, sort_keys=True, separators=(',', ':')),
                        state, json.dumps(result)))
        return result

    def test_fetch_failure_is_terminal_only_after_clean_readback_and_never_replayed(self):
        docker = [{'kind':'primary', 'branch':'main', 'head':NEW, 'clean':True,
                   'origin':ORIGIN, 'targetEligible':True}]
        failed = subprocess.CompletedProcess([], 128, b'', b'fatal: Authentication failed for secret-url')
        with mock.patch('mirror_api.pull.docker_inventory', return_value={'sample':docker}), \
             mock.patch('mirror_api.pull.windows_status', return_value=self.observed), \
             mock.patch('mirror_api.pull.github_main', return_value={'head':NEW, 'error':None}), \
             mock.patch('mirror_api.pull.subprocess.run', return_value=failed) as git:
            first = self.worker.run(self.body, repo_id='sample', allowed_repos={'sample'})
            second = self.worker.run(self.body, repo_id='sample', allowed_repos={'sample'})
        self.assertEqual('FAILED', first['state'])
        self.assertEqual('FETCH', first['phase'])
        self.assertFalse(first['mergeStarted'])
        self.assertFalse(first['checkoutChanged'])
        self.assertEqual({'cause':'GIT_AUTH_REQUIRED', 'exitCode':128}, first['diagnostic'])
        self.assertEqual(first['diagnostic'], second['diagnostic'])
        self.assertNotIn('secret-url', json.dumps(first))
        git.assert_called_once()
        with self.worker._db() as db:
            self.assertNotIn('secret-url', db.execute('SELECT result FROM pull_receipts').fetchone()[0])

    def test_legacy_fetch_failure_reconciles_on_unchanged_head_without_any_git_effect(self):
        self.seed(error='WINDOWS_FETCH_FAILED', timingsMs={'windowsFetch':350})
        with mock.patch('mirror_api.pull.windows_status', return_value=self.observed), \
             mock.patch.object(self.worker, '_execute') as effect:
            result = self.worker.run(self.body, repo_id='sample', allowed_repos={'sample'})
        self.assertEqual('FAILED', result['state'])
        self.assertEqual('FETCH_REASON_NOT_RECORDED', result['diagnostic']['cause'])
        self.assertEqual({'windowsFetch':350}, result['timingsMs'])
        effect.assert_not_called()

    def test_unsafe_readback_preserves_uncertainty_and_blocks_new_uuid_across_workers(self):
        self.seed(error='WINDOWS_FETCH_FAILED')
        other = PullWorker(self.mirror, self.path)
        with mock.patch('mirror_api.pull.windows_status', return_value={**self.observed, 'clean':False}):
            result = other.status(REQUEST_ID, repo_id='sample')
            self.assertEqual('UNCERTAIN', result['state'])
            self.assertTrue(other.safety('sample')['blocked'])
            with mock.patch.object(other, '_execute') as effect:
                with self.assertRaisesRegex(PullDenied, 'REPOSITORY_OPERATION_PENDING'):
                    other.run({**self.body, 'requestId':'22345678-1234-4234-8234-123456789abc'},
                              repo_id='sample', allowed_repos={'sample'})
                effect.assert_not_called()

    def test_merge_started_with_old_head_remains_uncertain_and_reports_fresh_observation(self):
        self.seed(error='WINDOWS_FF_FAILED', phase='MERGE', mergeStarted=True, workerFinished=True)
        with mock.patch('mirror_api.pull.windows_status', return_value=self.observed):
            result = self.worker.status(REQUEST_ID)
        self.assertEqual('UNCERTAIN', result['state'])
        self.assertEqual(OLD, result['observation']['head'])
        self.assertIn('checkedAt', result['observation'])

    def test_target_readback_records_convergence_without_claiming_exclusive_causation(self):
        self.seed(error='WINDOWS_READBACK_UNCONFIRMED', phase='READBACK', mergeStarted=True)
        with mock.patch('mirror_api.pull.windows_status', return_value={**self.observed, 'head':NEW}):
            result = self.worker.status(REQUEST_ID)
        self.assertEqual('EXECUTED', result['state'])
        self.assertEqual('TARGET_READBACK', result['evidence'])

    def test_dispatched_receipt_is_never_completed_from_an_intermediate_readback(self):
        self.seed(state='DISPATCHED', phase='MERGE', mergeStarted=True)
        with mock.patch('mirror_api.pull.windows_status') as read:
            self.assertEqual('DISPATCHED', self.worker.status(REQUEST_ID)['state'])
            self.assertTrue(self.worker.safety('sample')['blocked'])
            read.assert_not_called()

    def test_new_session_cannot_read_old_receipt_and_preview_fences_unknown_effect(self):
        self.seed(error='UNKNOWN')
        fresh = BrowserPull(self.mirror, self.path)
        with self.assertRaisesRegex(PullDenied, 'PULL_RECEIPT_NOT_OWNED'):
            fresh.status('sample', 'new-session', REQUEST_ID)
        with mock.patch('mirror_api.pull.windows_status', return_value=self.observed):
            preview = fresh.preview('sample', 'new-session')
        self.assertFalse(preview['available'])
        self.assertIn('PREVIOUS_PULL_UNRESOLVED', preview['reasons'])
        self.assertEqual({'blocked','pendingCount','checkedAt'}, set(fresh.safety('sample')))

    def test_new_session_can_preview_after_internal_legacy_failure_recovery(self):
        self.seed(error='WINDOWS_FETCH_FAILED')
        fresh = BrowserPull(self.mirror, self.path)
        with mock.patch('mirror_api.pull.windows_status', return_value=self.observed):
            preview = fresh.preview('sample', 'new-session')
        self.assertTrue(preview['available'])
        with self.assertRaisesRegex(PullDenied, 'PULL_RECEIPT_NOT_OWNED'):
            fresh.status('sample', 'new-session', REQUEST_ID)

    def test_diagnostic_projection_never_returns_raw_stderr(self):
        cases = {'could not resolve host':'GIT_NETWORK_FAILED', 'SSL certificate problem':'GIT_TLS_FAILED',
                 'cannot lock ref':'GIT_LOCKED', 'repository not found':'GIT_REMOTE_UNAVAILABLE',
                 'arbitrary credential':'GIT_FETCH_FAILURE_UNCLASSIFIED'}
        for message, cause in cases.items():
            self.assertEqual({'cause':cause, 'exitCode':128}, fetch_failure(message, 128))
