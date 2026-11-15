"""Windows GitHub transport fixtures; no live GitHub, Gateway or Git effects."""
import copy
import os
import sys
from unittest import mock
import unittest

from mirror_api import windows_github as gh
from mirror_api.operations import github_main, worktree_pr_ci

REPO = 'fixture'
OWNER = gh.OWNER
MAIN, HEAD = 'a' * 40, 'b' * 40
BRANCH = 'task/fixture'


def raw_pull(*, state='open', merged=False, owner=OWNER):
    return dict(number=1, title='Fixture', state=state, draft=False, merged=merged,
        head=dict(ref=BRANCH, sha=HEAD, repo={'full_name': owner+'/'+REPO}),
        base=dict(ref='main', sha=MAIN, repo={'full_name': OWNER+'/'+REPO}))


def raw_run(*, head=HEAD, conclusion='success'):
    return dict(id=7, name='Source CI', event='pull_request', status='completed',
        conclusion=conclusion, head_branch=BRANCH, head_sha=head, run_number=7,
        run_attempt=1, created_at='2026-11-15T00:00:00Z', updated_at='2026-11-15T00:00:01Z')


class WindowsFixture(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.routes = {
            '': {'full_name': OWNER+'/'+REPO, 'owner': {'login': OWNER}},
            'branches/main': {'name': 'main', 'commit': {'sha': MAIN}},
            'branches/task%2Ffixture': {'name': BRANCH, 'commit': {'sha': HEAD}},
            'branches?page=1&per_page=100': [{'name':'main','commit':{'sha':MAIN}},
                                          {'name':BRANCH,'commit':{'sha':HEAD}}],
            'pulls?state=all&page=1&per_page=30': [raw_pull()],
            'pulls/1': raw_pull(state='closed', merged=True),
            'actions/runs?page=1&per_page=30': {'workflow_runs':[raw_run()]},
            'commits/'+HEAD+'/check-runs?per_page=100': {'total_count':1,'check_runs':[
                dict(id=9,name='Checks',head_sha=HEAD,status='completed',conclusion='success')]},
            'commits?sha='+MAIN+'&page=1&per_page=50': [{'sha':MAIN},{'sha':HEAD}],
        }
        self.stack = []
        for patch in [mock.patch.object(gh, 'is_windows', return_value=True),
                      mock.patch.object(gh.shutil, 'which', return_value='/fixture/gh.exe'),
                      mock.patch.object(gh, 'run_gh', side_effect=self.read)]:
            self.stack.append(patch.start()); self.addCleanup(patch.stop)

    def read(self, argv, *, timeout):
        self.assertEqual(['/fixture/gh.exe','api','--hostname','github.com','--method','GET'],argv[:6])
        self.assertGreater(timeout,0);self.assertLessEqual(timeout,25)
        prefix='repos/'+OWNER+'/'+REPO
        self.assertTrue(argv[-1] == prefix or argv[-1].startswith(prefix+'/'))
        route=argv[-1][len(prefix):].lstrip('/')
        self.calls.append(route)
        value=self.routes[route]
        if isinstance(value,Exception):raise value
        return copy.deepcopy(value)

    def test_fresh_main_uses_only_identity_and_addressed_main(self):
        result=gh.snapshot(REPO,main_only=True)
        self.assertEqual(MAIN,result['head'])
        self.assertEqual(['','branches/main'],self.calls)
        self.assertIsNone(result['pullRequests']);self.assertIsNone(result['workflowRuns'])
        self.assertEqual('windows-gh',result['source'])

    def test_full_snapshot_preserves_exact_sha_ci_semantics(self):
        result=gh.snapshot(REPO,worktree_branches=[BRANCH])
        result['githubRepository']=REPO
        pr,ci=worktree_pr_ci({'branch':BRANCH},result)
        self.assertEqual('OPEN',pr['status'])
        self.assertEqual('OBSERVED_SUCCESS_NOT_REQUIRED_GATE',ci['status'])
        self.assertFalse(ci['requiredGateVerified'])
        self.assertEqual(HEAD,ci['headSha'])
        self.assertNotIn('branches?page=1&per_page=100',self.calls)

    def test_closed_pr_requires_matching_detail_and_merge_proof(self):
        self.routes['pulls?state=all&page=1&per_page=30']=[raw_pull(state='closed')]
        result=gh.snapshot(REPO,worktree_branches=[BRANCH])
        self.assertTrue(result['pullRequests'][0]['merged'])
        self.assertTrue(result['pullRequests'][0]['mergeVerified'])
        self.assertIn('pulls/1',self.calls)

    def test_fork_with_same_branch_and_sha_is_not_local_worktree_pr(self):
        self.routes['pulls?state=all&page=1&per_page=30']=[raw_pull(owner='OtherOwner')]
        result=gh.snapshot(REPO,worktree_branches=[BRANCH]);result['githubRepository']=REPO
        pr,ci=worktree_pr_ci({'branch':BRANCH},result)
        self.assertEqual('NONE',pr['status']);self.assertEqual('NO_PR',ci['status'])

    def test_deleted_fork_is_preserved_but_never_matches_local_worktree(self):
        deleted=raw_pull(state='closed');deleted['number']=2;deleted['head']['repo']=None
        deleted['head']['ref']='task/gone'
        self.routes['pulls?state=all&page=1&per_page=30']=[deleted,raw_pull()]
        result=gh.snapshot(REPO,worktree_branches=[BRANCH]);result['githubRepository']=REPO
        self.assertEqual(2,len(result['pullRequests']))
        self.assertIsNone(result['pullRequests'][0]['headRepository'])
        self.assertEqual('OPEN',worktree_pr_ci({'branch':BRANCH},result)[0]['status'])

    def test_ancestry_uses_captured_main_sha_not_mutable_default_branch(self):
        result=gh.snapshot(REPO,main_only=True,main_ancestor_sha=HEAD)
        self.assertEqual('behind',result['mainHistory']['relation'])
        self.assertIn('commits?sha='+MAIN+'&page=1&per_page=50',self.calls)

    def test_wrong_owner_or_renamed_repository_stops_before_branch_read(self):
        for identity in ['OtherOwner/fixture',OWNER+'/renamed']:
            self.routes['']={'full_name':identity,'owner':{'login':OWNER}}
            self.calls.clear();result=gh.snapshot(REPO,main_only=True)
            self.assertIsNone(result['head']);self.assertEqual([''],self.calls)
            self.assertEqual('GITHUB_REPOSITORY_MISMATCH',result['errors']['github'])

    def test_permission_timeout_and_missing_main_are_unknown_without_retry(self):
        for code in ['GITHUB_HTTP_403','GITHUB_HTTP_429','GITHUB_TIMEOUT']:
            self.routes['branches/main']=gh.GitHubReadError(code);self.calls.clear()
            result=gh.snapshot(REPO,main_only=True)
            self.assertIsNone(result['head']);self.assertEqual(code,result['errors']['branches'])
            self.assertEqual(['','branches/main'],self.calls)
        self.routes['branches/main']=gh.GitHubReadError('GITHUB_HTTP_404')
        result=gh.snapshot(REPO,main_only=True)
        self.assertIsNone(result['head']);self.assertEqual('GITHUB_MAIN_UNVERIFIED',result['errors']['branches'])

    def test_malformed_ci_does_not_hide_main_or_fabricate_success(self):
        self.routes['actions/runs?page=1&per_page=30']={'workflow_runs':[raw_run(head='bad')]}
        result=gh.snapshot(REPO,worktree_branches=[BRANCH])
        self.assertEqual(MAIN,result['head']);self.assertIsNone(result['workflowRuns'])
        self.assertIn('workflowRuns',result['errors'])

    def test_read_credentials_never_fall_back_to_docker_gateway(self):
        with mock.patch('mirror_api.operations.gateway_pr_ci',side_effect=AssertionError('no Gateway')) as gateway, \
             mock.patch('mirror_api.operations.run',side_effect=AssertionError('no Docker GitHub bridge')):
            value=github_main('local-alias','https://github.com/'+OWNER+'/'+REPO+'.git',details=False)
        self.assertEqual(MAIN,value['head']);self.assertEqual('windows-gh',value['source'])
        gateway.assert_not_called()

    def test_missing_cli_and_non_windows_are_explicit_and_make_no_request(self):
        for attr,value,expected in [('is_windows',False,'GITHUB_WINDOWS_REQUIRED'),
                                    ('which',None,'GITHUB_CLI_UNAVAILABLE')]:
            target=gh if attr=='is_windows' else gh.shutil
            with mock.patch.object(target,attr,return_value=value):
                result=gh.snapshot(REPO,main_only=True)
            self.assertEqual(expected,result['errors']['github'])
        self.assertEqual([],self.calls)

    def test_invalid_repo_branch_ancestor_and_expired_budget_never_reach_transport(self):
        for repo in ['../secret','x?command=write','OtherOwner/repo','-R']:
            self.assertIsNone(gh.snapshot(repo,main_only=True)['head'])
        for branch in ['task/../secret','main?x=1','-R other/repo']:
            self.assertIsNone(gh.snapshot(REPO,worktree_branches=[branch])['head'])
        self.assertIsNone(gh.snapshot(REPO,main_ancestor_sha='main')['head'])
        client=gh.WindowsGitHub(REPO);client.deadline=0
        with self.assertRaisesRegex(gh.GitHubReadError,'GITHUB_TIMEOUT'):client.verify_repository()
        self.assertEqual([],self.calls)


class PipeLimits(unittest.TestCase):
    def setUp(self):
        patch=mock.patch.object(gh,'is_windows',return_value=True)
        patch.start();self.addCleanup(patch.stop)

    def command(self, code, timeout=3):
        return gh.run_gh([sys.executable,'-c',code],timeout=timeout)

    def test_pipe_json_and_fixed_auth_failure_without_raw_output(self):
        self.assertEqual({'ok':True},self.command('print(\'{"ok":true}\')'))
        with self.assertRaisesRegex(gh.GitHubReadError,'^GITHUB_HTTP_403$'):
            self.command('import sys;print("secret-token HTTP 403",file=sys.stderr);sys.exit(1)')
        self.assertEqual('GITHUB_READ_UNAVAILABLE',str(gh.GitHubReadError('secret-token')))

    def test_duplicate_json_nonfinite_and_invalid_json_are_refused(self):
        for raw in ['{"a":1,"a":2}','{"a":NaN}','{"a":1e999}','not-json']:
            with self.subTest(raw=raw), self.assertRaisesRegex(gh.GitHubReadError,'GITHUB_RESPONSE_INVALID'):
                self.command('print('+repr(raw)+')')

    def test_stdout_stderr_and_time_limits_kill_the_child(self):
        with mock.patch.object(gh,'MAX_OUTPUT',100):
            with self.assertRaisesRegex(gh.GitHubReadError,'GITHUB_RESPONSE_TOO_LARGE'):
                self.command('print("x"*10000)')
        with mock.patch.object(gh,'MAX_STDERR',100):
            with self.assertRaisesRegex(gh.GitHubReadError,'GITHUB_RESPONSE_TOO_LARGE'):
                self.command('import sys;print("x"*10000,file=sys.stderr)')
        with self.assertRaisesRegex(gh.GitHubReadError,'GITHUB_TIMEOUT'):
            self.command('import time;time.sleep(5)',timeout=.05)

    def test_environment_removes_credentials_debug_host_and_proxy_overrides(self):
        with mock.patch.dict(os.environ,{'GH_TOKEN':'secret','GITHUB_TOKEN':'secret',
             'GH_HOST':'evil.invalid','GH_DEBUG':'api','HTTPS_PROXY':'http://evil.invalid',
             'GH_CONFIG_DIR':'/unreviewed'}):
            env=gh.gh_environment()
        self.assertFalse(set(['GH_TOKEN','GITHUB_TOKEN','GH_HOST','GH_DEBUG','HTTPS_PROXY','GH_CONFIG_DIR']) & env.keys())
        self.assertEqual('1',env['GH_PROMPT_DISABLED']);self.assertEqual('0',env['GIT_TERMINAL_PROMPT'])

    def test_non_windows_refuses_child_process_even_for_direct_transport_call(self):
        with mock.patch.object(gh,'is_windows',return_value=False), mock.patch.object(gh.subprocess,'Popen') as child:
            with self.assertRaisesRegex(gh.GitHubReadError,'GITHUB_WINDOWS_REQUIRED'):
                self.command('print("never")')
            child.assert_not_called()


if __name__ == '__main__':
    unittest.main()
