"""Offline API and Git observations; no Windows, Docker or network services required."""
from http.client import HTTPConnection
import json
from pathlib import Path
from unittest import mock
import subprocess
import tempfile
import threading
import unittest
from mirror_api.operations import Mirror, ObservationError, windows_status, relation_to_sha, evaluate_actions
from mirror_api.server import MirrorServer


class FakeMirror:
    def inventory(self, remote=False):
        return {"schemaVersion": 1, "checkedAt": "2026-11-07T00:00:00Z", "repositories": [],
                "errors": [] if not remote else ["REMOTE_TEST"]}


class APIClientTests(unittest.TestCase):
    def setUp(self):
        self.server = MirrorServer(("127.0.0.1", 0), FakeMirror())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, cookie=None, host=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Host": host or f"127.0.0.1:{self.server.server_port}"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        body = response.read()
        status = response.status
        set_cookie = response.getheader("Set-Cookie")
        location = response.getheader("Location")
        conn.close()
        return status, body, set_cookie, location

    def login(self):
        status, _, cookie, location = self.request("GET", "/session?boot=" + self.server.bootstrap_token)
        self.assertEqual(302, status)
        self.assertEqual("/", location)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        return cookie.split(";", 1)[0]

    def test_session_read_and_write_denied(self):
        self.assertEqual(401, self.request("GET", "/api/v1/repositories")[0])
        cookie = self.login()
        status, body, *_ = self.request("GET", "/api/v1/repositories?remote=true", cookie)
        self.assertEqual(200, status)
        self.assertEqual(["REMOTE_TEST"], json.loads(body)["errors"])
        self.assertEqual(403, self.request("POST", "/api/v1/repositories", cookie)[0])
        self.assertEqual(400, self.request("GET", "/api/v1/repositories?unknown=x", cookie)[0])
        self.assertEqual(403, self.request("GET", "/", cookie, host="attacker.invalid")[0])
        self.assertEqual(403, self.request("GET", "/session?boot=" + self.server.bootstrap_token + "x")[0])

    def test_bootstrap_one_time(self):
        token = self.server.bootstrap_token
        self.login()
        self.assertEqual(403, self.request("GET", "/session?boot=" + token)[0])


class GitStatusTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "Repos"
        self.base.mkdir()
        self.clone = self.base / "sample"
        self.clone.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Sample")
        self.git("config", "user.email", "sample@example.invalid")
        self.git("branch", "-m", "main")
        (self.clone / "file.txt").write_text("base")
        self.git("add", "file.txt")
        self.git("commit", "-qm", "base")
        self.git("remote", "add", "origin", "https://github.com/AlexPastukhh/sample.git")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.clone), *args],
                                       stderr=subprocess.DEVNULL, timeout=10).decode().strip()

    def test_windows_clean_dirty_and_missing(self):
        value = windows_status(self.base, "sample", "https://github.com/AlexPastukhh/sample.git")
        self.assertTrue(value["clean"])
        self.assertTrue(value["originValid"])
        self.assertEqual("main", value["branch"])
        self.assertEqual("WINDOWS_CLONE_NOT_FOUND", windows_status(self.base, "other", None)["error"])
        (self.clone / "file.txt").write_text("changed")
        value = windows_status(self.base, "sample", "https://github.com/AlexPastukhh/sample.git")
        self.assertFalse(value["clean"])
        self.assertEqual(1, value["changedCount"])

    def test_ancestry_without_fetch(self):
        old = self.git("rev-parse", "HEAD")
        (self.clone / "file.txt").write_text("next")
        self.git("add", "file.txt")
        self.git("commit", "-qm", "next")
        new = self.git("rev-parse", "HEAD")
        self.assertEqual("behind", relation_to_sha(self.clone, old, new))
        self.assertEqual("ahead", relation_to_sha(self.clone, new, old))
        self.assertEqual("equal", relation_to_sha(self.clone, old, old))
        self.assertEqual("unknown", relation_to_sha(self.clone, old, "f"*40))

    def test_pull_never_enabled_without_worker(self):
        windows = {"error": None, "originValid": True, "branch": "main",
                   "clean": True, "relation": "behind"}
        github = {"error": None, "head": "a"*40}
        action = evaluate_actions(windows, github, approved_sha="a"*40)["pull_main"]
        self.assertFalse(action["available"])
        self.assertIn("GATEWAY_WINDOWS_WORKER_NOT_CONNECTED", action["reasons"])

    def test_inventory_failed_not_empty(self):
        mirror = Mirror(self.base)
        with mock.patch("mirror_api.operations.docker_inventory", side_effect=ObservationError("DOCKER_DOWN")):
            with self.assertRaisesRegex(ObservationError, "DOCKER_DOWN"):
                mirror.inventory()


if __name__ == "__main__":
    unittest.main()

class DurablePullTests(unittest.TestCase):
    def setUp(self):
        from mirror_api.pull import PullWorker
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.worker = PullWorker(Mirror(base / 'Repos'), base / 'runtime' / 'receipts.sqlite')
        self.payload = {'requestId': '12345678-1234-4234-8234-123456789abc',
                        'repoId': 'fixture', 'expectedHead': 'a'*40,
                        'targetSha': 'b'*40,
                        'origin': 'https://github.com/AlexPastukhh/fixture.git'}

    def test_denied_target_never_reserves_receipt(self):
        from mirror_api.pull import PullDenied
        with self.assertRaisesRegex(PullDenied, 'REPOSITORY_NOT_ALLOWED'):
            self.worker.run(self.payload, repo_id='fixture', allowed_repos=frozenset())
        self.assertEqual('NOT_FOUND', self.worker.status(self.payload['requestId'])['state'])

    def test_durable_replay_never_calls_worker_twice(self):
        from mirror_api.pull import PullDenied
        with mock.patch.object(self.worker, '_execute', side_effect=ObservationError('STOP')) as worker:
            first = self.worker.run(self.payload, repo_id='fixture', allowed_repos={'fixture'})
            second = self.worker.run(self.payload, repo_id='fixture', allowed_repos={'fixture'})
        self.assertEqual('UNCERTAIN', first['state'])
        self.assertEqual(first, second)
        worker.assert_called_once()
        with self.assertRaisesRegex(PullDenied, 'REQUEST_ID_CONFLICT'):
            self.worker.run({**self.payload,'targetSha':'c'*40}, repo_id='fixture', allowed_repos={'fixture'})
        with self.worker._db() as db:
            count = db.execute('select count(*) from pull_receipts').fetchone()[0]
        self.assertEqual(1, count)

    def test_preflight_never_fetches_without_docker_registration(self):
        with mock.patch('mirror_api.pull.docker_inventory', return_value={}), mock.patch('mirror_api.pull.subprocess.run') as fetch:
            result = self.worker.run(self.payload, repo_id='fixture', allowed_repos={'fixture'})
        self.assertEqual('UNCERTAIN', result['state'])
        fetch.assert_not_called()


class GatewayRouteTests(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        from mirror_api.server import GatewayHandler
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), GatewayHandler)
        self.server.daemon_threads = True
        self.server.gateway_secret = 'x'*40
        self.server.allowed_repos = {'fixture'}
        self.server.mirror = FakeMirror()
        class Worker:
            def status(self, request_id, repo_id=None):
                return {'requestId':request_id,'state':'NOT_FOUND','head':None}
            def run(self,*args,**kwargs):
                raise AssertionError('write must not be called without auth')
        self.server.worker = Worker()
        t = threading.Thread(target=self.server.serve_forever,daemon=True)
        t.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(t.join, 2)
        self.addCleanup(self.server.shutdown)

    def test_gateway_requires_credential_not_browser_cookie(self):
        conn = HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        conn.request('GET','/gateway/v1/repositories/fixture',headers={'Cookie':'mirror_session=x'})
        response=conn.getresponse()
        self.assertEqual(401,response.status)
        response.read();conn.close()
        conn=HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        conn.request('POST','/gateway/v1/repositories/fixture/pull-main',body='{}')
        response=conn.getresponse()
        self.assertEqual(401,response.status)
        response.read();conn.close()

class DockerOwnerActionsTests(unittest.TestCase):
    def test_explicit_allowlist_and_frozen_selected_worktree(self):
        from mirror_api.docker_actions import DockerActions, ActionDenied
        import base64
        source = DockerActions({'fixture'})
        display = base64.b64encode(b'?? task.txt').decode()
        preview_raw = 'FORTIS_DOCKER_STAGE_V1\ttask/task-one\t'+'a'*40+'\t'+'b'*64+'\t1\nF\t0\t'+display
        with mock.patch.object(source,'invoke',side_effect=[preview_raw,'STAGED\tfixture\ttask/task-one\t1']) as invoke:
            item=source.preview_checked('owner-session','fixture','task-one','stage')
            self.assertEqual(['?? task.txt'],item['files'])
            result=source.execute('owner-session',item['previewId'],{'indices':[0]})
            self.assertEqual('stage',result['operation'])
            self.assertIn('--stage',invoke.call_args_list[1].args)
            self.assertEqual('task-one',invoke.call_args_list[1].args[1])
        with self.assertRaises(ActionDenied):
            source.execute('owner-session',item['previewId'],{'indices':[0]})
        with self.assertRaises(ActionDenied):
            source.preview_checked('owner-session','fortis-approvals','task-one','stage')

    def test_wrong_session_never_runs_action(self):
        from mirror_api.docker_actions import DockerActions, ActionDenied
        action = DockerActions({'fixture'})
        with mock.patch.object(action,'invoke',return_value='FORTIS_DOCKER_PUSH_V1\ttask/t\t'+'a'*40+'\t'+'b'*40) as call:
            preview=action.preview_checked('owner','fixture','t','push')
            with self.assertRaises(ActionDenied):
                action.execute('other',preview['previewId'],{})
        call.assert_called_once()

class RealGitFastForwardFixture(unittest.TestCase):
    """Actual local Git objects and ff-only merge; no GitHub or Windows host required."""
    def test_pinned_merge_and_durable_no_repeat(self):
        from mirror_api.pull import PullWorker
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            repos=base/'Repos';repos.mkdir()
            source=base/'source';source.mkdir()
            def git(path,*args):
                return subprocess.check_output(['git','-C',str(path),*args],
                    stderr=subprocess.DEVNULL,timeout=10).decode().strip()
            git(source,'init','-q')
            git(source,'config','user.name','Fixture')
            git(source,'config','user.email','fixture@example.invalid')
            git(source,'branch','-m','main')
            (source/'README').write_text('base\n')
            git(source,'add','README');git(source,'commit','-qm','base')
            bare=base/'origin.git'
            subprocess.check_call(['git','clone','-q','--bare',str(source),str(bare)])
            windows=repos/'sample'
            subprocess.check_call(['git','clone','-q',str(bare),str(windows)])
            old=git(windows,'rev-parse','HEAD')
            (source/'README').write_text('merged\n')
            git(source,'add','README');git(source,'commit','-qm','merged')
            target=git(source,'rev-parse','HEAD')
            git(source,'push',str(bare),'main')
            origin='https://github.com/AlexPastukhh/sample.git'
            git(windows,'remote','set-url','origin',origin)
            worker=PullWorker(Mirror(repos),base/'receipts.sqlite')
            request={'requestId':'12345678-1234-4234-8234-123456789abc',
                'repoId':'sample','expectedHead':old,'targetSha':target,'origin':origin}
            original_run=subprocess.run
            fetches=[]
            def local_fetch(argv,*args,**kwargs):
                if 'fetch' in argv and 'origin' in argv:
                    fetches.append(1)
                    cmd=list(argv)
                    index=cmd.index('origin')
                    cmd[index]=str(bare)
                    cmd=[x.replace('protocol.file.allow=never','protocol.file.allow=always') for x in cmd]
                    return original_run(cmd,*args,**kwargs)
                return original_run(argv,*args,**kwargs)
            registered={'sample':[{'kind':'primary','origin':origin}]}
            with mock.patch('mirror_api.pull.docker_inventory',return_value=registered), \
                mock.patch('mirror_api.pull.github_main',return_value={'head':target,'error':None}), \
                mock.patch('mirror_api.pull.subprocess.run',side_effect=local_fetch):
                first=worker.run(request,repo_id='sample',allowed_repos={'sample'})
                second=worker.run(request,repo_id='sample',allowed_repos={'sample'})
            self.assertEqual('EXECUTED',first['state'],str(first))
            self.assertEqual(first,second)
            self.assertEqual(target,git(windows,'rev-parse','HEAD'))
            self.assertEqual('',git(windows,'status','--porcelain'))
            self.assertEqual(1,len(fetches))

class PullReconcileTests(unittest.TestCase):
    def test_uncertain_receipt_can_be_proven_without_new_fetch(self):
        from mirror_api.pull import PullWorker
        with tempfile.TemporaryDirectory() as folder:
            worker=PullWorker(Mirror(Path(folder)/'Repos'),Path(folder)/'state.sqlite')
            value={'requestId':'12345678-1234-4234-8234-123456789abc',
                   'repoId':'fixture','expectedHead':'a'*40,'targetSha':'b'*40,
                   'origin':'https://github.com/AlexPastukhh/fixture.git'}
            with mock.patch.object(worker,'_execute',side_effect=ObservationError('NO_REPLY')):
                initial=worker.run(value,repo_id='fixture',allowed_repos={'fixture'})
            self.assertEqual('UNCERTAIN',initial['state'])
            proof={'error':None,'head':'b'*40,'branch':'main','clean':True,'originValid':True}
            with mock.patch('mirror_api.pull.windows_status',return_value=proof), \
                 mock.patch.object(worker,'_execute') as no_execute:
                result=worker.status(value['requestId'])
                self.assertEqual('EXECUTED',result['state'])
                self.assertEqual(result,worker.run(value,repo_id='fixture',allowed_repos={'fixture'}))
                no_execute.assert_not_called()

class GatewayAuthenticatedRoutes(unittest.TestCase):
    """Route tests use loopback fixture; actual TLS/Windows live acceptance separate."""
    def setUp(self):
        from http.server import ThreadingHTTPServer
        from mirror_api.server import GatewayHandler
        self.server=ThreadingHTTPServer(('127.0.0.1',0),GatewayHandler)
        self.server.daemon_threads=True
        self.server.gateway_secret='q'*40
        self.server.allowed_repos={'fixture'}
        class Fake:
            def inventory(self,remote=False):
                return {'repositories':[{'repoId':'fixture','docker':{},'windows':{},'github':{},'actions':{}}]}
        self.server.mirror=Fake()
        class Worker:
            def status(self, request_id, repo_id=None):
                return {'requestId':request_id,'state':'NOT_FOUND','head':None}
            def run(self,value,*,repo_id,allowed_repos):
                assert repo_id == 'fixture'
                assert value['repoId'] == repo_id
                assert repo_id in allowed_repos
                return {'state':'EXECUTED','requestId':value['requestId'],'head':value['targetSha'],'checkedAt':'fixed'}
        self.server.worker=Worker()
        worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(worker.join,2)
        self.addCleanup(self.server.shutdown)

    def request(self,method,path,value=None,headers=None):
        conn=HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        hdr={'Authorization':'Bearer '+'q'*40}
        if headers:hdr.update(headers)
        data=None if value is None else json.dumps(value)
        conn.request(method,path,body=data,headers=hdr)
        response=conn.getresponse();status=response.status;raw=response.read();conn.close()
        return status,json.loads(raw)

    def test_read_exact_scope_and_closed_write_schema(self):
        status,value=self.request('GET','/gateway/v1/repositories/fixture')
        self.assertEqual(200,status)
        self.assertEqual('fixture',value['repository']['repoId'])
        self.assertEqual(404,self.request('GET','/gateway/v1/repositories/unlisted')[0])
        p={'requestId':'12345678-1234-4234-8234-123456789abc','repoId':'fixture',
           'expectedHead':'a'*40,'targetSha':'b'*40,'origin':'https://github.com/AlexPastukhh/fixture.git'}
        headers={'Content-Type':'application/json'}
        self.assertEqual(400,self.request('POST','/gateway/v1/repositories/fixture/pull-main',p,headers)[0])
        headers['Idempotency-Key']=p['requestId']
        status,value=self.request('POST','/gateway/v1/repositories/fixture/pull-main',p,headers)
        self.assertEqual(200,status)
        self.assertEqual('EXECUTED',value['state'])
        self.assertEqual(404,self.request('POST','/gateway/v1/repositories/unlisted/pull-main',p,headers)[0])

class CrossProcessReservationTests(unittest.TestCase):
    def test_pending_same_repo_blocks_other_request_id(self):
        from mirror_api.pull import PullWorker, PullDenied
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=PullWorker(Mirror(root/'Repos'),root/'receipts.sqlite')
            value={'requestId':'12345678-1234-4234-8234-123456789abc',
                'repoId':'fixture','expectedHead':'a'*40,'targetSha':'b'*40,
                'origin':'https://github.com/AlexPastukhh/fixture.git'}
            frozen=json.dumps(value,sort_keys=True,separators=(',',':'))
            with first._db() as db:
                db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                    (value['requestId'],frozen,'DISPATCHED',None))
            second=PullWorker(Mirror(root/'Repos'),root/'receipts.sqlite')
            next_value={**value,'requestId':'22345678-1234-4234-8234-123456789abc'}
            with mock.patch.object(second,'_execute') as mutate:
                with self.assertRaisesRegex(PullDenied,'REPOSITORY_OPERATION_PENDING'):
                    second.run(next_value,repo_id='fixture',allowed_repos={'fixture'})
                mutate.assert_not_called()
