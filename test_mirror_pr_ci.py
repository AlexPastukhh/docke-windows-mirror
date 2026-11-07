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

    def test_registered_repo_scope_required(self):
        class Gateway:
            def __init__(self,**kwargs): pass
            def post(self,path,value):
                assert path=='/v1/workspace/capabilities'
                return {'repository':'other'}
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
        github={'head':SHA1,'branches':{'main':SHA1,BRANCH:SHA2},'error':None,'checkedAt':'now', 'requiredCI':'NOT_VERIFIED'}
        with mock.patch('mirror_api.operations.docker_inventory',return_value={REPO:[tree,task]}),\
             mock.patch('mirror_api.operations.windows_status',return_value=win),\
             mock.patch('mirror_api.operations.github_main',return_value=github),\
             mock.patch('mirror_api.operations.gateway_pr_ci',return_value={**data,'schema':'fortis.github.mirror.v1'}):
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
        from fortis_approvals.core import Denied
        row={**pull(), 'state':'closed', 'merged':False}
        class Client:
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
