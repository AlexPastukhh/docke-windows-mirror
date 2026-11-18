"""P5 offline preflight cannot turn a locally green fixture into an owner GO."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.mirror_read_v2_cutover_preflight import inspect, main, MANUAL_GATES

SHA = 'f' * 40
GEN = 'a' * 64
ORIGIN = 'https://github.com/AlexPastukhh/docke-windows-mirror.git'


class OwnerReadV2PreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='mirror-p5-test-')
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.profile = base / 'Owner'
        self.repo = self.profile / 'Repos' / 'docke-windows-mirror'
        self.repo.mkdir(parents=True)
        (self.repo / '.git').mkdir()
        self.appdata = self.profile / 'AppData' / 'Local'
        self.db = self.appdata / 'FortisMirror' / 'pull-receipts.sqlite3'
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(b'SQLite format 3\x00' + b'\x00' * 128)
        self.values = {
            'gitRoot': str(self.repo),
            'branch': 'main',
            'origin': ORIGIN,
            'head': SHA,
            'trackingMain': SHA,
            'status': '',
        }
        self.calls = []

    def git(self, root, command):
        self.calls.append(tuple(command))
        mapping = {
            ('rev-parse','--show-toplevel'):'gitRoot',
            ('symbolic-ref','--quiet','--short','HEAD'):'branch',
            ('remote','get-url','origin'):'origin',
            ('rev-parse','--verify','HEAD'):'head',
            ('rev-parse','--verify','refs/remotes/origin/main'):'trackingMain',
            ('status','--porcelain=v1','--untracked-files=all'):'status',
        }
        assert root == self.repo
        assert tuple(command) in mapping, command
        return self.values[mapping[tuple(command)]]

    def check(self, *, sha=SHA, generation=GEN):
        return inspect(repo_root=self.repo, userprofile=self.profile,
                       localappdata=self.appdata, expected_source_sha=sha,
                       machine_generation=generation, git_reader=self.git)

    @staticmethod
    def failures(report):
        return {x['code'] for x in report['checks'] if x['state']=='STOP'}

    def test_pass_still_requires_owner_go_and_does_not_create_any_state(self):
        before = self.db.read_bytes()
        report = self.check()
        self.assertEqual(report['state'],'MANUAL_REQUIRED')
        self.assertIs(report['cutoverAuthorized'],False)
        self.assertEqual(report['manualGates'],list(MANUAL_GATES))
        self.assertEqual(report['scope'],
                         {'inventory':'mirror-pilot','repositories':['fortis-approvals'],
                          'state':'SOURCE_EXPECTATION_ONLY'})
        self.assertFalse(self.failures(report))
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(len(self.calls),6)
        self.assertEqual(list(self.db.parent.iterdir()),[self.db])
        self.assertEqual(set(report),
                         {'schemaVersion','state','cutoverAuthorized','scope','checks',
                          'manualGates'})

    def test_exact_sha_source_origin_branch_tracking_or_dirty_are_stop(self):
        cases = (
            ('branch','task/feature','SOURCE_NOT_MAIN'),
            ('origin','https://evil.invalid','SOURCE_ORIGIN_DENIED'),
            ('head','b'*40,'EXACT_SOURCE_SHA_MISMATCH'),
            ('trackingMain','b'*40,'TRACKING_MAIN_NOT_EQUAL'),
            ('status',' M runtime.py','WINDOWS_SOURCE_DIRTY'),
            ('gitRoot','/other','GIT_TOPLEVEL_MISMATCH'),
        )
        for field,value,code in cases:
            with self.subTest(field=field):
                old=self.values[field];self.values[field]=value
                report=self.check()
                self.assertEqual(report['state'],'STOP')
                self.assertIn(code,self.failures(report))
                self.values[field]=old
        report=self.check(sha='b'*40)
        self.assertIn('EXACT_SOURCE_SHA_MISMATCH',self.failures(report))

    def test_missing_or_malformed_ledger_is_stop_no_db_creation(self):
        self.db.unlink()
        report=self.check()
        self.assertIn('LEDGER_MISSING_OR_INVALID',self.failures(report))
        self.assertFalse(self.db.exists())
        self.db.write_bytes(b'not SQLite')
        self.assertIn('LEDGER_MISSING_OR_INVALID',self.failures(self.check()))
        self.assertEqual(self.db.read_bytes(),b'not SQLite')

    def test_source_clone_and_owner_roots_fail_before_git(self):
        self.repo.rename(self.profile / 'Repos' / 'renamed')
        self.assertIn('WINDOWS_PRIMARY_CLONE_UNVERIFIED',
                      self.failures(self.check()))
        self.assertEqual(self.calls,[])
        self.repo = self.profile / 'Repos' / 'renamed'
        self.assertFalse(any('git' in entry for entry in self.calls))
        report=inspect(repo_root=self.repo,userprofile=self.profile,
                       localappdata=None,expected_source_sha=SHA,
                       machine_generation=GEN,git_reader=self.git)
        self.assertEqual(report['state'],'STOP')

    def test_missing_generation_and_malformed_sha_are_hard_stop(self):
        self.assertIn('OWNER_REVIEWED_MACHINE_GENERATION_REQUIRED',
                      self.failures(self.check(generation=None)))
        self.assertIn('OWNER_REVIEWED_MACHINE_GENERATION_REQUIRED',
                      self.failures(self.check(generation='A'*64)))
        self.assertIn('SOURCE_SHA_PIN_REQUIRED',
                      self.failures(self.check(sha='a'*39)))

    def test_git_command_is_strictly_local_without_index_locks(self):
        from tools.mirror_read_v2_cutover_preflight import _git_read
        response = subprocess.CompletedProcess([],0,stdout='main\n',stderr='')
        with patch('subprocess.run',return_value=response) as call:
            self.assertEqual(_git_read(self.repo,
                            ['symbolic-ref','--quiet','--short','HEAD']),'main')
        kwargs=call.call_args.kwargs
        self.assertEqual(kwargs['env']['GIT_OPTIONAL_LOCKS'],'0')
        self.assertEqual(kwargs['env']['GIT_TERMINAL_PROMPT'],'0')
        self.assertEqual(call.call_args.args[0][:5],
                         ['git','-c','core.fsmonitor=false','-C',str(self.repo)])
        self.assertFalse(kwargs['check'])

    def test_no_unsupported_os_fallback(self):
        with patch('sys.platform','linux'),patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(['--expected-source-sha',SHA,
                                   '--machine-generation',GEN]),2)
        report=json.loads(out.getvalue())
        self.assertEqual(report['state'],'STOP')
        self.assertEqual(report['checks'][0]['code'],'WINDOWS_OWNER_REQUIRED')
        self.assertFalse(report['cutoverAuthorized'])


if __name__=='__main__':
    unittest.main()
