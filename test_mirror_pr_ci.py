"""Exact-SHA PR/CI projection; no live API, no writes, no Git credential."""
import importlib.util
import json
from pathlib import Path
from unittest import mock
import unittest
from mirror_api.operations import Mirror, ObservationError, gateway_pr_ci, worktree_pr_ci

SOURCE=Path(__file__).parent/'tools'/'Export-Mirror-GitHub-Read.py'
spec=importlib.util.spec_from_file_location('fortis_mirror_gateway_reads',SOURCE)
reader=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
SHA1='a'*40
SHA2='b'*40
SHA3='c'*40
REPO='fortis-approvals'
BRANCH='task/feature-one'


def pull(sha=SHA2):
    return dict(number=18,title='Feature one',state='open',draft=False,merged=False,
                url='https://github.com/AlexPastukhh/fortis-approvals/pull/18',
                head={'ref':BRANCH,'sha':sha},base={'ref':'main','sha':SHA1})


def run(sha,number,conclusion,name='Source CI'):
    return dict(id=number,name=name,event='pull_request',status='completed',
                conclusion=conclusion,headBranch=BRANCH,headSha=sha,
                runNumber=number,runAttempt=1,createdAt='2026-11-07',updatedAt='2026-11-07',
                url='https://github.com/AlexPastukhh/fortis-approvals/actions/runs/'+str(number))


class ReaderTests(unittest.TestCase):
    def test_gateway_only_typed_reads_and_exact_sha_checks(self):
        class Client:
            def list_branches(self,repo,*,page,per_page):
                assert repo==REPO and page==1 and per_page==100
                return {'items':[{'name':'main','sha':SHA1},{'name':BRANCH,'sha':SHA2}]}
            def list_pull_requests(self,repo,*,state,page,per_page):
                self_check=(repo==REPO and state=='all' and per_page==30 and page==1)
                assert self_check
                return {'items':[pull()],'page':page,'perPage':per_page}
            def list_workflow_runs(self,repo,*,page,per_page):
                assert repo==REPO and page==1 and per_page==30
                return {'items':[run(SHA2,1,'success'),run(SHA2,2,'failure'),
                                 run(SHA3,3,'success')], 'totalCount':3}
            def list_check_runs(self,repo,ref):
                assert repo==REPO and ref==SHA2
                return {'items':[{'name':'Test','headSha':SHA2,'status':'completed','conclusion':'failure'}],
                        'totalCount':1}
        result=reader.collect(Client(),REPO)
        self.assertEqual(SHA1,result['head'])
        self.assertEqual(SHA2,result['branches'][BRANCH])
        self.assertEqual(1,len(result['pullRequests']))
        self.assertEqual(3,len(result['workflowRuns']))
        self.assertEqual(1,len(result['checkRuns'][SHA2]['items']))
        pr,ci=worktree_pr_ci({'branch':BRANCH},{'pullRequests':result['pullRequests'],
            'workflowRuns':result['workflowRuns'],'checkRuns':result['checkRuns'],
            'truncated':result['truncated'],'branches':{BRANCH:SHA2}})
        self.assertEqual('OPEN',pr['status'])
        self.assertEqual('OBSERVED_FAILURE',ci['status'])
        self.assertEqual([2],[r['id'] for r in ci['workflowRuns']])
        self.assertEqual(SHA2,ci['headSha'])
        self.assertFalse(ci['requiredGateVerified'])

    def test_main_history_proves_docker_behind_only_for_pinned_main(self):
        calls=[]
        class Client:
            def list_branches(self,repo,*,page,per_page):
                return {'items':[{'name':'main','sha':SHA1}]}
            def list_commits(self,repo,*,page,per_page):
                calls.append((repo,page,per_page))
                return {'items':[{'sha':SHA1},{'sha':SHA3},{'sha':SHA2}]}
        result=reader.collect(Client(),REPO,main_only=True,main_ancestor_sha=SHA2)
        self.assertEqual('behind',result['mainHistory']['relation'])
        self.assertEqual(SHA1,result['mainHistory']['head'])
        self.assertEqual(SHA2,result['mainHistory']['candidate'])
        self.assertEqual([(REPO,1,50)],calls)
        self.assertEqual({},result['errors'])
        self.assertIsNone(result['pullRequests'],'Fast read never expands PR/CI')

    def test_main_history_absence_or_moving_head_never_invents_divergence(self):
        class Client:
            def __init__(self,first): self.first=first
            def list_branches(self,repo,*,page,per_page):
                return {'items':[{'name':'main','sha':SHA1}]}
            def list_commits(self,repo,*,page,per_page):
                return {'items':[{'sha':self.first},{'sha':SHA3}]}
        absent=reader.collect(Client(SHA1),REPO,main_only=True,main_ancestor_sha=SHA2)
        self.assertEqual('unknown',absent['mainHistory']['relation'])
        self.assertEqual({},absent['errors'])
        moved=reader.collect(Client(SHA3),REPO,main_only=True,main_ancestor_sha=SHA2)
        self.assertIsNone(moved['mainHistory'])
        self.assertEqual('GATEWAY_MAIN_HISTORY_UNAVAILABLE',moved['errors']['mainHistory'])
        self.assertEqual(SHA1,moved['head'], 'Failed history read cannot hide known branch SHA')

    def test_main_history_equal_sha_skips_extra_gateway_requests(self):
        class Client:
            def list_branches(self,repo,*,page,per_page):
                return {'items':[{'name':'main','sha':SHA1}]}
            def list_commits(self,repo,*,page,per_page):
                self.fail('Unexpected list_commits')
        result=reader.collect(Client(),REPO,main_only=True,main_ancestor_sha=SHA1)
        self.assertIsNone(result['mainHistory'])
        self.assertEqual({},result['errors'])

    def test_registered_repo_scope_required(self):
        class Gateway:
            def __init__(self,**kwargs): pass
            def post(self,path,value):
                assert path=='/v1/workspace/capabilities'
                return {'owner':'AlexPastukhh','repository':'other','dynamicRepositories':False}
        with mock.patch.object(reader,'WorkflowTransport',Gateway), \
                mock.patch.object(reader,'GitHubClient') as github:
            value=reader.inspect(REPO)
        self.assertEqual('GATEWAY_REPOSITORY_NOT_REGISTERED',value['errors']['scope'])
        self.assertIsNone(value['pullRequests'])
        github.assert_not_called()

    def test_unknown_not_conflated_with_no_pr_or_success(self):
        pr,ci=worktree_pr_ci({'branch':BRANCH},{'pullRequests':None,
            'workflowRuns':None,'checkRuns':{},'truncated':{}})
        self.assertEqual('UNVERIFIED',pr['status'])
        self.assertEqual('UNVERIFIED',ci['status'])
        pr,ci=worktree_pr_ci({'branch':BRANCH},{'pullRequests':[],
            'workflowRuns':[],'checkRuns':{},'truncated':{'pullRequests':True}})
        self.assertEqual('UNKNOWN_TRUNCATED',pr['status'])
        self.assertEqual('UNVERIFIED',ci['status'])

    def test_workflow_run_belongs_to_specific_branch_and_pr_sha(self):
        pr,ci=worktree_pr_ci({'branch':BRANCH},{'pullRequests':[pull()],
            'workflowRuns':[run(SHA3,30,'success'),run(SHA2,31,'success'),
               {**run(SHA2,32,'success'), 'event':'push'},
               {**run(SHA2,33,'success'),'headBranch':'another'}],
            'checkRuns':{},'truncated':{},'branches':{BRANCH:SHA3}})
        self.assertFalse(pr['headMatchesPublished'])
        self.assertEqual('OBSERVED_SUCCESS_NOT_REQUIRED_GATE',ci['status'])
        self.assertEqual([31],[r['id'] for r in ci['workflowRuns']])
        self.assertFalse(ci['requiredGateVerified'])

    def test_read_error_preserved_as_unknown(self):
        with mock.patch('mirror_api.operations.run',side_effect=ObservationError('NO_GATEWAY')):
            value=gateway_pr_ci(REPO)
        self.assertIsNone(value['pullRequests'])
        self.assertEqual('GATEWAY_READ_UNAVAILABLE',value['errors']['gateway'])

    def test_inventory_attaches_pr_ci_only_from_gateway(self):
        tree={'repoId':REPO,'kind':'primary','branch':'main','origin':'https://github.com/AlexPastukhh/fortis-approvals.git'}
        task={'repoId':REPO,'kind':'worktree','branch':BRANCH,'head':SHA2}
        data={'pullRequests':[pull()], 'workflowRuns':[run(SHA2,1,'success')],
              'checkRuns':{},'errors':{},'truncated':{}}
        win={'exists':False,'error':'WINDOWS_CLONE_NOT_FOUND','relation':'unknown',
             'originValid':False,'branch':None,'clean':None}
        github={'head':SHA1,'branches':{'main':SHA1,BRANCH:SHA2},'error':None,'checkedAt':'now',
                'requiredCI':'NOT_VERIFIED','pullRequests':data['pullRequests'],
                'workflowRuns':data['workflowRuns'],'checkRuns':data['checkRuns'],'truncated':{}}
        with mock.patch('mirror_api.operations.docker_inventory',return_value={REPO:[tree,task]}),\
             mock.patch('mirror_api.operations.windows_status',return_value=win),\
             mock.patch('mirror_api.operations.github_main',return_value=github):
            result=Mirror('/tmp/none').inventory(remote=True)
        item=result['repositories'][0]
        self.assertEqual('OPEN',item['docker']['worktrees'][1]['pullRequest']['status'])
        self.assertEqual('OBSERVED_SUCCESS_NOT_REQUIRED_GATE',item['docker']['worktrees'][1]['ci']['status'])
        self.assertEqual('NOT_VERIFIED',item['github']['requiredCI'])


if __name__=='__main__':unittest.main()

class ClosedPRVerificationTests(unittest.TestCase):
    def test_merged_is_from_individual_read_not_closed_list_guess(self):
        row={**pull(), 'state':'closed', 'merged':False}
        class Client:
            def list_branches(self,repo,*,page,per_page):
                assert repo==REPO and page==1 and per_page==100
                return {'items':[{'name':'main','sha':SHA1},{'name':BRANCH,'sha':SHA2}]}
            def list_pull_requests(self,repo,*,state,page,per_page):
                return {'items':[row] if page==1 else [],'page':page,'perPage':per_page}
            def get_pull_request(self,repo,number):
                assert number==row['number']
                return {'pullRequest':{**row,'merged':True}}
            def list_workflow_runs(self,repo,*,page,per_page):
                return {'items':[], 'totalCount':0}
        data=reader.collect(Client(),REPO)
        self.assertEqual({},data['errors'])
        self.assertTrue(data['pullRequests'][0]['merged'])
        self.assertTrue(data['pullRequests'][0]['mergeVerified'])
        pr,_=worktree_pr_ci({'branch':BRANCH},{**data,'branches':{BRANCH:SHA2}})
        self.assertEqual('MERGED',pr['status'])

    def test_unavailable_details_stays_uncertain(self):
        Denied=reader.Denied
        row={**pull(), 'state':'closed', 'merged':False}
        class Client:
            def list_branches(self,repo,*,page,per_page):
                assert repo==REPO and page==1 and per_page==100
                return {'items':[{'name':'main','sha':SHA1},{'name':BRANCH,'sha':SHA2}]}
            def list_pull_requests(self,repo,*,state,page,per_page):
                return {'items':[row], 'page':page, 'perPage':per_page}
            def get_pull_request(self,repo,number):
                raise Denied('SCOPE_DENIED')
            def list_workflow_runs(self,repo,*,page,per_page):
                return {'items':[], 'totalCount':0}
        data=reader.collect(Client(),REPO)
        self.assertIn('pullRequestDetails',data['errors'])
        pr,_=worktree_pr_ci({'branch':BRANCH},{**data,'branches':{BRANCH:SHA2}})
        self.assertEqual('CLOSED_OR_MERGED_UNVERIFIED',pr['status'])
        self.assertFalse(pr['data']['merged'])
        self.assertFalse(pr['data']['mergeVerified'])


class ScopedPerformanceTests(unittest.TestCase):
    """Select/all checks never make per-PR reads for absent local worktrees."""

    def test_main_only_reads_branches_without_fetching_any_pr_or_ci(self):
        class Client:
            def list_branches(self,repo,*,page,per_page):
                return {'items':[{'name':'main','sha':SHA1}]}
            def list_pull_requests(self,*args,**kwargs):
                raise AssertionError('Main check must not read PR list')
            def list_workflow_runs(self,*args,**kwargs):
                raise AssertionError('Main check must not read CI history')
            def get_pull_request(self,*args,**kwargs):
                raise AssertionError('Main check must not read individual PRs')
            def list_check_runs(self,*args,**kwargs):
                raise AssertionError('Main check must not read check runs')
        data=reader.collect(Client(),REPO,worktree_branches=[BRANCH],main_only=True)
        self.assertEqual(SHA1,data['head'])
        self.assertEqual({'main':SHA1},data['branches'])
        self.assertIsNone(data['pullRequests'])
        self.assertIsNone(data['workflowRuns'])
        self.assertEqual({},data['checkRuns'])
        self.assertEqual({},data['errors'])
        self.assertIn('branches',data['timingsMs'])
        self.assertNotIn('pullRequests',data['timingsMs'])
        self.assertFalse(worktree_pr_ci({'branch':BRANCH},data)[1]['requiredGateVerified'],
                         'Main-only branch snapshot must never imply CI verification')

    def test_no_local_task_skips_closed_detail_and_open_check_without_losing_lists(self):
        closed={**pull(), 'number': 11, 'state': 'closed', 'merged': False}
        opened=pull(sha=SHA3)
        class Client:
            def list_branches(self,repo,*,page,per_page):
                return {'items':[{'name':'main','sha':SHA1},{'name':BRANCH,'sha':SHA3}]}
            def list_pull_requests(self,repo,*,state,page,per_page):
                return {'items':[closed,opened]}
            def list_workflow_runs(self,repo,*,page,per_page):
                return {'items':[run(SHA3,2,'success')]}
            def get_pull_request(self,*args):
                raise AssertionError('Absent closed PR must not cause N Gateway reads')
            def list_check_runs(self,*args):
                raise AssertionError('Absent open PR must not cause N Gateway reads')
        value=reader.collect(Client(),REPO,worktree_branches=[])
        self.assertEqual(SHA1,value['head'])
        self.assertEqual(2,len(value['pullRequests']))
        self.assertEqual(1,len(value['workflowRuns']))
        self.assertFalse(value['pullRequests'][0]['mergeVerified'])
        self.assertEqual({},value['checkRuns'])
        self.assertEqual({},value['errors'])
        self.assertIn('total',value['timingsMs'])

    def test_only_current_worktree_closed_pr_is_individually_verified(self):
        closed={**pull(), 'number': 21, 'state': 'closed', 'merged': False}
        unrelated={**closed,'number': 22,
                   'head': {'ref':'task/archive-not-present','sha':SHA3}}
        class Client:
            def __init__(self):self.details=[]
            def list_branches(self,repo,*,page,per_page):
                return {'items':[{'name':'main','sha':SHA1}]}
            def list_pull_requests(self,repo,*,state,page,per_page):
                return {'items':[closed.copy(),unrelated.copy()]}
            def list_workflow_runs(self,repo,*,page,per_page):
                return {'items':[]}
            def get_pull_request(self,repo,number):
                self.details.append(number)
                if number!=21:raise AssertionError('Unexpected unrelated PR detail read')
                return {'pullRequest':{**closed,'merged':True}}
        client=Client()
        value=reader.collect(client,REPO,worktree_branches=[BRANCH])
        self.assertEqual([21],client.details)
        self.assertTrue(value['pullRequests'][0]['mergeVerified'])
        self.assertTrue(value['pullRequests'][0]['merged'])
        self.assertFalse(value['pullRequests'][1]['mergeVerified'])
        pr,_=worktree_pr_ci({'branch':BRANCH},{**value,'branches':{BRANCH:SHA2}})
        self.assertEqual('MERGED',pr['status'])

    def test_current_open_worktree_still_reads_exact_head_checks(self):
        opened=pull(sha=SHA2)
        class Client:
            def __init__(self):self.heads=[]
            def list_branches(self,repo,*,page,per_page):
                return {'items':[{'name':'main','sha':SHA1}]}
            def list_pull_requests(self,repo,*,state,page,per_page):
                return {'items':[opened]}
            def list_workflow_runs(self,repo,*,page,per_page):
                return {'items':[run(SHA2,2,'failure')]}
            def list_check_runs(self,repo,sha):
                self.heads.append(sha)
                return {'items':[{'headSha':sha,'name':'Source CI','status':'completed','conclusion':'failure'}],
                        'totalCount':1}
        client=Client()
        value=reader.collect(client,REPO,worktree_branches=[BRANCH])
        self.assertEqual([SHA2],client.heads)
        pr,ci=worktree_pr_ci({'branch':BRANCH},{**value,'branches':{BRANCH:SHA2}})
        self.assertEqual('OPEN',pr['status'])
        self.assertEqual('OBSERVED_FAILURE',ci['status'])
        self.assertFalse(ci['requiredGateVerified'])

    def test_three_independent_read_groups_are_concurrent(self):
        import threading
        import time
        class Client:
            def __init__(self):
                self.barrier=threading.Barrier(3,timeout=3)
            def _synced(self):
                self.barrier.wait()
            def list_branches(self,repo,*,page,per_page):
                self._synced()
                return {'items':[{'name':'main','sha':SHA1}]}
            def list_pull_requests(self,repo,*,state,page,per_page):
                self._synced()
                return {'items':[]}
            def list_workflow_runs(self,repo,*,page,per_page):
                self._synced()
                return {'items':[]}
        value=reader.collect(Client(),REPO,worktree_branches=[])
        self.assertEqual({},value['errors'])
        self.assertEqual(SHA1,value['head'])


class DynamicGatewayTests(unittest.TestCase):
    def test_dynamic_repo_uses_token_scoped_typed_reads(self):
        class Gateway:
            def __init__(self,**kwargs): pass
            def post(self,path,value):
                assert path=='/v1/workspace/capabilities'
                return {'owner':'AlexPastukhh','repository':'fortis-approvals',
                        'dynamicRepositories':True}
        class Typed:
            def __init__(self,*,gateway): pass
            def list_branches(self,repo,*,page,per_page):
                return {'items':[{'name':'main','sha':SHA1}],'page':page}
            def list_pull_requests(self,repo,*,state,page,per_page):
                return {'items':[],'page':page}
            def list_workflow_runs(self,repo,*,page,per_page):
                return {'items':[],'page':page}
        with mock.patch.object(reader,'WorkflowTransport',Gateway), \
             mock.patch.object(reader,'GitHubClient',Typed):
            data=reader.inspect('docke-windows-mirror')
        self.assertEqual(SHA1,data['head'])
        self.assertEqual({},data['errors'])

    def test_ci_checkout_without_gateway_package_stays_readonly_unknown(self):
        import builtins
        original_import=builtins.__import__
        def absent_fortis(name,*args,**kwargs):
            if name.startswith('fortis_approvals'):
                raise ModuleNotFoundError(name=name)
            return original_import(name,*args,**kwargs)
        spec=importlib.util.spec_from_file_location(
            'mirror_reader_no_gateway_for_test',SOURCE)
        isolated=importlib.util.module_from_spec(spec)
        with mock.patch('builtins.__import__',side_effect=absent_fortis):
            spec.loader.exec_module(isolated)
        self.assertIsNone(isolated.WorkflowTransport)
        self.assertIsNone(isolated.GitHubClient)
        result=isolated.inspect('docke-windows-mirror')
        self.assertEqual('GATEWAY_TYPED_CLIENT_MISSING',result['errors']['gateway'])
        self.assertIsNone(result['head'])

    def test_missing_branch_read_is_unknown_not_empty_clean(self):
        class Client:
            def list_branches(self,repo,*,page,per_page):
                Denied=reader.Denied
                raise Denied('SCOPE_DENIED')
            def list_pull_requests(self,repo,*,state,page,per_page):
                return {'items':[]}
            def list_workflow_runs(self,repo,*,page,per_page):
                return {'items':[]}
        data=reader.collect(Client(),REPO)
        self.assertIsNone(data['head'])
        self.assertEqual('GATEWAY_BRANCH_READ_UNAVAILABLE',data['errors']['branches'])

    def test_github_origin_maps_registered_local_folder_to_verified_owner_repo(self):
        from mirror_api.operations import github_main
        with mock.patch('mirror_api.operations.windows_github_snapshot') as source:
            for origin in ('', None):
                self.assertEqual('ORIGIN_MISSING', github_main('ai-conspects',origin)['error'])
            for origin in (
                'https://github.com/OtherOwner/theory_conspects_and_banks.git',
                'https://example.invalid/AlexPastukhh/theory_conspects_and_banks.git',
                'https://github.com/AlexPastukhh/theory_conspects_and_banks.git?x=1',
                'https://github.com/AlexPastukhh/theory_conspects_and_banks.git/other',
                'file:///repo/secret',
                'https://github.com/AlexPastukhh/..',
            ):
                self.assertEqual('ORIGIN_NOT_ALLOWED',
                    github_main('ai-conspects',origin)['error'], origin)
            source.assert_not_called()
            source.return_value={'head':SHA1,'branches':{'main':SHA1},
                'pullRequests':[],'workflowRuns':[],'checkRuns':{},'errors':{},'truncated':{}}
            result=github_main('ai-conspects',
                'https://github.com/AlexPastukhh/theory_conspects_and_banks.git')
            self.assertEqual(SHA1,result['head'])
            self.assertEqual('ai-conspects',result['repoId'], 'Local folder key must be unchanged')
            self.assertEqual('theory_conspects_and_banks',result['githubRepository'])
            self.assertEqual('windows-gh', result['source'])
            source.assert_called_once_with('theory_conspects_and_banks')
            source.reset_mock()
            github_main('docke-windows-mirror',
                'https://github.com/AlexPastukhh/docke-windows-mirror.git')
            source.assert_called_once_with('docke-windows-mirror')

    def test_origin_alias_inventory_keeps_local_paths_and_guarded_pull(self):
        from mirror_api.operations import Mirror
        origin='https://github.com/AlexPastukhh/theory_conspects_and_banks.git'
        primary=dict(kind='primary',branch='main',head=SHA1,clean=True,
                     targetEligible=True,origin=origin,status='registered')
        windows=dict(exists=True,head=SHA2,branch='main',origin=origin,
                     originValid=True,error=None,clean=True,relation='unknown')
        with mock.patch('mirror_api.operations.docker_inventory', return_value={'ai-conspects':[primary]}), \
             mock.patch('mirror_api.operations.windows_status',return_value=windows) as win, \
             mock.patch('mirror_api.operations.windows_github_snapshot',return_value={
                 'head':SHA1,'branches':{'main':SHA1},'pullRequests':None,
                 'workflowRuns':None,'checkRuns':{},'errors':{},'truncated':{}}) as gateway:
            data=Mirror('/tmp/safe-repos').inventory(remote=True,repo='ai-conspects')
        row=data['repositories'][0]
        self.assertEqual('ai-conspects',row['repoId'])
        self.assertEqual('theory_conspects_and_banks',row['github']['githubRepository'])
        self.assertEqual(SHA1,row['github']['head'])
        gateway.assert_called_once_with('theory_conspects_and_banks',main_only=True,
                                        main_ancestor_sha=SHA1)
        self.assertEqual('ai-conspects',win.call_args.args[1],
                         'Windows clone name must remain local repo folder')
        self.assertEqual(origin,win.call_args.args[2],
                         'The exact Docker origin still binds Windows trust')
        self.assertTrue(row['actions']['pull_main_browser']['available'],
                        'Only the same guarded preview conditions may permit Pull')
        untrusted=dict(windows,originValid=False)
        with mock.patch('mirror_api.operations.docker_inventory',return_value={'ai-conspects':[primary]}), \
             mock.patch('mirror_api.operations.windows_status',return_value=untrusted), \
             mock.patch('mirror_api.operations.windows_github_snapshot',return_value={
                 'head':SHA1,'branches':{'main':SHA1},'pullRequests':None,
                 'workflowRuns':None,'checkRuns':{},'errors':{},'truncated':{}}):
            bad=Mirror('/tmp/safe-repos').inventory(remote=True,repo='ai-conspects')
        self.assertFalse(bad['repositories'][0]['actions']['pull_main_browser']['available'],
                         'Different Windows origin must block Pull even for a mapped alias')

    def test_gateway_denial_propagates_bounded_http_code(self):
        class Client:
            def list_branches(self,repo,*,page,per_page):
                raise reader.Denied('FORTIS_HTTP_403')
        row=reader.collect(Client(),REPO,main_only=True)
        self.assertIsNone(row['head'])
        self.assertEqual('GATEWAY_HTTP_403',row['errors']['branches'])
        class OtherClient:
            def list_branches(self,repo,*,page,per_page):
                raise reader.Denied('SECRET_BEARER_OR_OTHER_FAILURE')
        other=reader.collect(OtherClient(),REPO,main_only=True)
        self.assertEqual('GATEWAY_BRANCH_READ_UNAVAILABLE',other['errors']['branches'],
                         'Unbounded provider exception text must not reach the browser')
        from mirror_api.operations import github_main
        row={**row,'errors':{'branches':'GITHUB_HTTP_403'}}
        with mock.patch('mirror_api.operations.windows_github_snapshot',return_value=row):
            value=github_main(REPO,'https://github.com/AlexPastukhh/fortis-approvals.git')
        self.assertEqual('GITHUB_HTTP_403',value['error'])
        self.assertIsNone(value['head'])
