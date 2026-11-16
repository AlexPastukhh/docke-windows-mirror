"""Offline owner-local pull tests: HTTP security, preview binding and receipts.

Fixtures never contact Windows GitHub/Docker. The existing PullWorker tests
separately exercise real Git objects and pinned fast-forward behavior.
"""
import copy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from mirror_api.browser_pull import BrowserPull
from mirror_api.operations import Mirror
from mirror_api.pull import PullDenied
from mirror_api.server import MirrorServer


OLD = 'a' * 40
NEW = 'b' * 40
ORIGIN = 'https://github.com/AlexPastukhh/sample.git'
REQUEST_ID = '12345678-1234-4234-8234-123456789abc'


def observed(*, relation='unknown'):
    return {'repositories': [{
        'repoId': 'sample',
        'docker': {'primary': {'kind': 'primary', 'branch': 'main',
                               'clean': True, 'status': 'registered', 'targetEligible': True,
                               'origin': ORIGIN, 'head': NEW}},
        'windows': {'source': 'windows', 'exists': True, 'branch': 'main',
                    'head': OLD, 'clean': True, 'error': None,
                    'originValid': True, 'origin': ORIGIN, 'relation': relation},
        'github': {'source': 'fortis-gateway', 'head': NEW, 'error': None,
                   'requiredCI': 'NOT_VERIFIED'},
    }]}


class FakeMirror:
    def __init__(self):
        self.data = observed()
        self.calls = []
        self.checks = []

    def check_repository(self, repo):
        self.checks.append(repo)
        return {'schemaVersion':1, 'checkedAt':'2026-11-09T12:00:00Z',
                'repositories':copy.deepcopy(self.data['repositories'])}

    def inventory(self, remote=False, repo=None):
        self.calls.append((remote, repo))
        return {**copy.deepcopy(self.data), 'checkedAt':'2026-11-09T12:00:00Z'}


class BrowserPullUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mirror = FakeMirror()
        self.mirror.windows_root = Path(self.tmp.name) / 'Repos'
        self.mirror.windows_root.mkdir(exist_ok=True)
        self.ticks = [100.0]
        self.pull = BrowserPull(self.mirror, Path(self.tmp.name) / 'receipts.sqlite', clock=lambda: self.ticks[0])

    def test_preview_pins_sha_and_unknown_requires_later_ff_proof(self):
        p = self.pull.preview('sample', 'session-A')
        self.assertTrue(p['available'])
        self.assertEqual('TO_BE_VERIFIED_AFTER_FETCH', p['fastForward'])
        self.assertEqual(OLD, p['windowsHead'])
        self.assertEqual(NEW, p['targetSha'])
        self.assertEqual('NOT_VERIFIED', p['requiredCI'])
        self.assertEqual([(True, 'sample')], self.mirror.calls)
        self.assertEqual('PREVIEWED', self.pull.status('sample', 'session-A', p['requestId'])['state'])
        with self.assertRaisesRegex(PullDenied, 'PULL_RECEIPT_NOT_OWNED'):
            self.pull.status('sample', 'session-B', p['requestId'])

    def test_blocked_previews_are_never_issued(self):
        cases = [
            ('docker dirty', ('docker', 'primary', 'clean'), False, 'DOCKER_MAIN_NOT_VERIFIED'),
            ('docker stale', ('docker', 'primary', 'head'), OLD, 'DOCKER_MAIN_NOT_AT_GITHUB_MAIN'),
            ('windows dirty', ('windows', 'clean'), False, 'WINDOWS_NOT_CLEAN'),
            ('windows branch', ('windows', 'branch'), 'task/wrong', 'WINDOWS_NOT_ON_MAIN'),
            ('windows origin', ('windows', 'originValid'), False, 'WINDOWS_ORIGIN_MISMATCH'),
            ('github offline', ('github', 'error'), 'GATEWAY_DOWN', 'GITHUB_MAIN_UNVERIFIED'),
            ('equal', ('windows', 'head'), NEW, 'WINDOWS_ALREADY_CURRENT'),
            ('ahead', ('windows', 'relation'), 'ahead', 'WINDOWS_NOT_BEHIND_GITHUB'),
        ]
        for label, path, value, reason in cases:
            with self.subTest(label=label):
                self.mirror.data = observed()
                node = self.mirror.data['repositories'][0]
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = value
                result = self.pull.preview('sample', 'one')
                self.assertFalse(result['available'])
                self.assertIn(reason, result['reasons'])
                self.assertIsNone(result['previewId'])
        self.assertFalse(self.pull.previews)

    def test_wrong_session_confirmation_and_expired_preview_never_execute(self):
        preview = self.pull.preview('sample', 'one')
        with self.assertRaisesRegex(PullDenied, 'PULL_CONFIRMATION_REQUIRED'):
            self.pull.execute('sample', 'one', preview['previewId'], 'yes')
        with self.assertRaisesRegex(PullDenied, 'PULL_PREVIEW_BINDING_MISMATCH'):
            self.pull.execute('sample', 'two', preview['previewId'], 'PULL_WINDOWS_MAIN')
        with self.assertRaisesRegex(PullDenied, 'PULL_PREVIEW_BINDING_MISMATCH'):
            self.pull.execute('other', 'one', preview['previewId'], 'PULL_WINDOWS_MAIN')
        self.assertEqual('PREVIEWED', self.pull.status('sample', 'one', preview['requestId'])['state'])
        self.ticks[0] += 121
        with self.assertRaisesRegex(PullDenied, 'PULL_PREVIEW_EXPIRED_OR_USED'):
            self.pull.execute('sample', 'one', preview['previewId'], 'PULL_WINDOWS_MAIN')
        self.assertEqual('NOT_FOUND', self.pull.worker.status(preview['requestId'])['state'])

    def test_one_click_dispatches_once_and_receipt_is_scoped(self):
        preview = self.pull.preview('sample', 'one')
        fake = {'requestId': preview['requestId'], 'state': 'EXECUTED',
                'head': NEW, 'checkedAt': 'now'}
        with mock.patch.object(self.pull.worker, '_execute', return_value=fake) as executor:
            first = self.pull.execute('sample', 'one', preview['previewId'], 'PULL_WINDOWS_MAIN')
            self.assertEqual(fake, first)
            self.assertEqual(fake, self.pull.status('sample', 'one', preview['requestId']))
            with self.assertRaisesRegex(PullDenied, 'PULL_PREVIEW_EXPIRED_OR_USED'):
                self.pull.execute('sample', 'one', preview['previewId'], 'PULL_WINDOWS_MAIN')
        executor.assert_called_once()
        sent = executor.call_args.args[0]
        self.assertEqual(set(sent), {'requestId', 'repoId', 'expectedHead', 'targetSha', 'origin'})
        self.assertEqual(OLD, sent['expectedHead'])
        self.assertEqual(NEW, sent['targetSha'])
        self.assertEqual(ORIGIN, sent['origin'])
        with self.assertRaisesRegex(PullDenied, 'PULL_RECEIPT_NOT_OWNED'):
            self.pull.status('sample', 'two', preview['requestId'])

    def test_uncertain_receipt_is_not_automatically_retried(self):
        preview = self.pull.preview('sample', 'one')
        with mock.patch.object(self.pull.worker, '_execute', side_effect=PullDenied('WINDOWS_PRECONDITIONS_CHANGED')) as ex:
            result = self.pull.execute('sample', 'one', preview['previewId'], 'PULL_WINDOWS_MAIN')
            self.assertEqual('UNCERTAIN', result['state'])
            self.assertEqual('UNCERTAIN', self.pull.status('sample', 'one', preview['requestId'])['state'])
            with self.assertRaises(PullDenied):
                self.pull.execute('sample', 'one', preview['previewId'], 'PULL_WINDOWS_MAIN')
        ex.assert_called_once()

    def test_execute_scopes_docker_fast_main_only_and_persists_phase_timings(self):
        """Guarded Pull uses no PR/CI fanout or whole-workspace Docker walk."""
        import types
        preview = self.pull.preview('sample', 'one')
        docker = [{'kind': 'primary', 'branch': 'main', 'head': NEW,
                   'clean': True, 'origin': ORIGIN, 'targetEligible': True}]
        current = {'error': None, 'branch': 'main', 'clean': True,
                   'head': OLD, 'originValid': True}
        with mock.patch('mirror_api.pull.docker_inventory',return_value={'sample':docker}) as inventory, \
             mock.patch('mirror_api.pull.windows_status',return_value=current) as win, \
             mock.patch('mirror_api.pull.github_main',return_value={'head':NEW,'error':None}) as gh, \
             mock.patch('mirror_api.pull.git',return_value=NEW) as git_read, \
             mock.patch('mirror_api.pull.command',return_value=types.SimpleNamespace(returncode=0)) as git_run, \
             mock.patch.object(self.pull.worker, '_confirmed_readback') as readback:
            receipt = self.pull.execute('sample','one',preview['previewId'],'PULL_WINDOWS_MAIN')
        self.assertEqual('EXECUTED',receipt['state'])
        self.assertEqual(NEW,receipt['head'])
        self.assertEqual(receipt,self.pull.status('sample','one',preview['requestId']),
                         'The same durable receipt exposes the timings read-only')
        inventory.assert_called_once_with('sample')
        self.assertEqual(2,win.call_count,'Windows preflight and post-fetch must remain')
        gh.assert_called_once_with('sample',ORIGIN,details=False)
        git_read.assert_called_once()
        readback.assert_called_once_with('sample',ORIGIN,NEW)
        self.assertEqual(3,git_run.call_count, 'fetch, ancestry, and fast-forward are still required')
        calls = [call.args[0] for call in git_run.call_args_list]
        self.assertTrue(any('fetch' in call for call in calls))
        self.assertTrue(any('merge-base' in call for call in calls))
        self.assertTrue(any('merge' in call and '--ff-only' in call for call in calls))
        timings=receipt['timingsMs']
        self.assertEqual({'dockerInventory','windowsPreflight','gatewayMain',
                          'windowsFetch','fetchHeadRead','windowsAfterFetch',
                          'gitAncestry','fastForward','windowsReadback','executeTotal'},set(timings))
        self.assertTrue(all(type(timings[k]) is int and timings[k]>=0 for k in timings))
        self.assertTrue(self.pull.worker.path.exists())

    def test_different_clones_can_execute_with_independent_worker_locks(self):
        from concurrent.futures import ThreadPoolExecutor
        from mirror_api.pull import PullWorker
        import uuid
        worker=PullWorker(self.mirror, Path(self.tmp.name)/'parallel.sqlite')
        rendezvous=threading.Barrier(2,timeout=3)
        def fake_run(payload, *, timings):
            rendezvous.wait()
            timings['gatewayMain']=1
            return {'requestId':payload['requestId'],'state':'EXECUTED',
                    'head':NEW,'checkedAt':'now'}
        def run(repo):
            request_id=str(uuid.uuid4())
            return worker.run({'requestId':request_id,'repoId':repo,
                               'expectedHead':OLD,'targetSha':NEW,
                               'origin':ORIGIN},repo_id=repo,
                              allowed_repos=frozenset({'sample','other'}))
        with mock.patch.object(worker,'_execute',side_effect=fake_run), \
             ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(run,'sample')
            second=pool.submit(run,'other')
            result1=first.result(timeout=5)
            result2=second.result(timeout=5)
        self.assertEqual('EXECUTED',result1['state'])
        self.assertEqual('EXECUTED',result2['state'])
        self.assertEqual(1,result1['timingsMs']['gatewayMain'])
        self.assertEqual(1,result2['timingsMs']['gatewayMain'])
        self.assertNotEqual(result1['requestId'],result2['requestId'])

    def test_gateway_main_mismatch_still_prevents_windows_git_and_is_measured(self):
        preview=self.pull.preview('sample','one')
        docker=[{'kind':'primary','branch':'main','head':NEW,'clean':True,
                 'origin':ORIGIN,'targetEligible':True}]
        current={'error':None,'branch':'main','clean':True,'head':OLD,'originValid':True}
        with mock.patch('mirror_api.pull.docker_inventory',return_value={'sample':docker}), \
             mock.patch('mirror_api.pull.windows_status',return_value=current), \
             mock.patch('mirror_api.pull.github_main',return_value={'head':OLD,'error':None}) as gh, \
             mock.patch('mirror_api.pull.command') as git_run:
            result=self.pull.execute('sample','one',preview['previewId'],'PULL_WINDOWS_MAIN')
        self.assertEqual('FAILED',result['state'])
        self.assertEqual('GITHUB_MAIN_CHANGED',result['error'])
        self.assertIn('gatewayMain',result['timingsMs'])
        self.assertIn('executeTotal',result['timingsMs'])
        gh.assert_called_once_with('sample',ORIGIN,details=False)
        git_run.assert_not_called()
        self.assertEqual('FAILED',self.pull.status('sample','one',preview['requestId'])['state'])
        with self.assertRaises(PullDenied):
            self.pull.execute('sample','one',preview['previewId'],'PULL_WINDOWS_MAIN')

    def test_readback_reconciliation_preserves_uncertain_receipt_timings(self):
        import sqlite3
        preview=self.pull.preview('sample','one')
        payload={'requestId':preview['requestId'],'repoId':'sample',
                 'expectedHead':OLD,'targetSha':NEW,'origin':ORIGIN}
        self.pull.requests[preview['requestId']]['state']='DISPATCHING'
        previous={'requestId':preview['requestId'],'state':'UNCERTAIN',
                  'head':None,'error':'WINDOWS_READBACK_UNCONFIRMED',
                  'timingsMs':{'gatewayMain':4500,'windowsFetch':300,'executeTotal':5200}}
        with sqlite3.connect(self.pull.worker.path) as db:
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (preview['requestId'],json.dumps(payload),'UNCERTAIN',json.dumps(previous)))
        with mock.patch('mirror_api.pull.windows_status', return_value={
                'error':None,'head':NEW,'branch':'main','clean':True,'originValid':True}) as read:
            result=self.pull.status('sample','one',preview['requestId'])
        self.assertEqual('EXECUTED',result['state'])
        self.assertEqual(previous['timingsMs'],result['timingsMs'])
        self.assertEqual(1,read.call_count,'No Git operation is allowed during reconciliation')
        self.assertEqual(result,self.pull.status('sample','one',preview['requestId']))

    def test_preview_returns_bounded_server_timings(self):
        self.mirror.data['repositories'][0]['readTimingsMs']={
            'dockerInventory':123,'windowsGit':80,'githubGateway':3100,'gitAncestry':8,
            'windowsFetch':999999999,'unrelated':45}
        preview=self.pull.preview('sample','one')
        timings=preview['timingsMs']
        self.assertGreaterEqual(timings['previewTotal'],0)
        self.assertEqual(123,timings['previewDocker'])
        self.assertEqual(80,timings['previewWindows'])
        self.assertEqual(3100,timings['previewGateway'])
        self.assertEqual(8,timings['previewAncestry'])
        self.assertNotIn('unrelated',timings)
        self.assertNotIn('windowsFetch',timings)

    def test_requires_durable_receipt_store(self):
        with mock.patch.dict('os.environ', {'LOCALAPPDATA': ''}):
            service = BrowserPull(self.mirror)
            self.assertFalse(service.configured)
            with self.assertRaisesRegex(PullDenied, 'PULL_RECEIPT_STORAGE_UNAVAILABLE'):
                service.preview('sample', 'one')
        self.assertEqual([], self.mirror.calls)


class BrowserPullHttpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.mirror = FakeMirror()
        self.mirror.windows_root = Path(tmp.name) / 'Repos'
        self.mirror.windows_root.mkdir()
        self.server = MirrorServer(('127.0.0.1', 0), self.mirror, receipt_db=Path(tmp.name) / 'runtime.sqlite')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)
        self.host = '127.0.0.1:' + str(self.server.server_port)
        self.origin = 'http://' + self.host
        self.cookie = None
        self.csrf = None

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        outgoing = {'Host': self.host}
        outgoing.update(headers or {})
        conn.request(method, path, body=body, headers=outgoing)
        result = conn.getresponse()
        code = result.status
        cookie = result.getheader('Set-Cookie')
        raw = result.read()
        conn.close()
        if raw and result.getheader('Content-Type', '').startswith('application/json'):
            value = json.loads(raw)
        else:
            value = None
        return code, value, cookie

    def login(self):
        code, _, cookie = self.request('GET', '/session?boot=' + self.server.bootstrap_token)
        self.assertEqual(302, code)
        self.cookie = cookie.split(';', 1)[0]
        code, value, _ = self.request('GET', '/api/v1/csrf', headers={'Cookie': self.cookie})
        self.assertEqual(200, code)
        self.csrf = value['csrf']

    def post(self, action, data='{}', *, headers=None, repo='sample'):
        base = {'Cookie': self.cookie, 'Origin': self.origin,
                'X-CSRF-Token': self.csrf, 'Content-Type': 'application/json'}
        base.update(headers or {})
        return self.request('POST', '/api/v1/repositories/' + repo + '/' + action,
                            data, headers=base)

    def test_owner_check_after_restart_observes_unresolved_receipt_without_fetch(self):
        self.login()
        self.mirror.windows_root = Path(self.server.browser_pull.receipt_db).parent / 'Repos'
        worker = self.server.browser_pull._worker()
        payload = {'requestId':REQUEST_ID,'repoId':'sample','expectedHead':OLD,'targetSha':NEW,'origin':ORIGIN}
        with worker._db() as db:
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                (REQUEST_ID,json.dumps(payload),'UNCERTAIN',json.dumps({
                    'requestId':REQUEST_ID,'state':'UNCERTAIN','head':None,'error':'UNKNOWN'})))
        with mock.patch('mirror_api.pull.windows_status',return_value={
                'head':OLD,'branch':'main','clean':True,'originValid':True,'error':None}):
            code,result,_ = self.post('check-windows')
            self.assertEqual(200,code)
            self.assertTrue(result['repository']['pullSafety']['blocked'])
            code,result,_ = self.request('GET','/api/v1/repositories/sample?remote=true',
                                        headers={'Cookie':self.cookie})
            self.assertEqual(200,code)
            self.assertTrue(result['repository']['pullSafety']['blocked'])
        self.assertEqual([],self.mirror.checks,'Unknown Pull never dispatches metadata fetch')
        code,result,_ = self.request('GET','/api/v1/repositories/sample/pull-results/'+REQUEST_ID,
                                    headers={'Cookie':self.cookie})
        self.assertEqual(404,code)
        self.assertEqual('PULL_RECEIPT_NOT_OWNED',result['error'])

    def test_authentication_host_origin_and_csrf_are_separate(self):
        code, value, _ = self.request('POST', '/api/v1/repositories/sample/pull-preview', '{}',
                                     {'Content-Type': 'application/json', 'Origin': self.origin})
        self.assertEqual(401, code)
        self.login()
        for title, override in [
            ('evil host', {'Host': 'example.invalid'}),
            ('evil origin', {'Origin': 'https://elsewhere.invalid'}),
            ('no origin', {'Origin': ''}),
            ('no csrf', {'X-CSRF-Token': ''}),
            ('bad csrf', {'X-CSRF-Token': 'bad'}),
            ('cross-site', {'Sec-Fetch-Site': 'cross-site'}),
        ]:
            with self.subTest(title=title):
                code, value, _ = self.post('pull-preview', headers=override)
                self.assertEqual(403, code)
        self.assertEqual([], self.mirror.calls)
        code, body, _ = self.request('GET', '/api/v1/capabilities', headers={'Cookie': self.cookie})
        self.assertEqual(200, code)
        self.assertFalse(body['readOnly'])
        self.assertEqual('local_owner_confirm', body['browserPull']['mode'])
        self.assertTrue(body['browserPull']['configured'])

    def test_owner_check_fetch_route_is_post_csrf_protected_and_only_selected(self):
        # The formerly read-only GET cannot silently fetch; explicit POST does.
        self.login()
        for label, headers in [('origin', {'Origin':'https://attacker.invalid'}),
                               ('csrf', {'X-CSRF-Token':''}),
                               ('site', {'Sec-Fetch-Site':'cross-site'})]:
            with self.subTest(label=label):
                self.assertEqual(403,self.post('check-windows',headers=headers)[0])
        self.assertEqual([],self.mirror.checks)
        self.assertEqual(409,self.post('check-windows','{"unexpected":true}')[0])
        self.assertEqual([],self.mirror.checks)
        self.assertEqual(405,self.request('POST','/api/v1/repositories/sample/check-windows?remote=true',
                                        body='{}',headers={'Cookie':self.cookie})[0])
        code,result,_=self.post('check-windows')
        self.assertEqual(200,code)
        self.assertEqual('sample',result['repository']['repoId'])
        self.assertEqual(['sample'],self.mirror.checks)
        self.assertEqual([],self.mirror.calls,'Owner check does not invoke Pull Preview or Execute')
        # Read-only GET still uses only observation inventory.
        code,_,_=self.request('GET','/api/v1/repositories/sample?remote=true',
                              headers={'Cookie':self.cookie})
        self.assertEqual(200,code)
        self.assertEqual(['sample'],self.mirror.checks)
        self.assertEqual([(True,'sample')],self.mirror.calls)

    def test_same_clone_check_and_pull_execute_are_serialized_different_clones_are_not(self):
        """A client-side Abort cannot bypass server's per-clone Git guard."""
        from concurrent.futures import ThreadPoolExecutor
        entered = threading.Event()
        release = threading.Event()
        real = self.mirror.check_repository
        def slow_check(repo):
            if repo == 'sample':
                entered.set()
                if not release.wait(timeout=4):
                    raise RuntimeError('Fixture check was not released')
            return real(repo)
        self.login()
        with mock.patch.object(self.mirror, 'check_repository', side_effect=slow_check), \
             ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.post,'check-windows')
            try:
                self.assertTrue(entered.wait(timeout=2))
                worker = self.server.browser_pull._worker()
                self.assertFalse(worker._repo_lock('sample').acquire(blocking=False),
                    'Same-clone Gateway worker is fenced while Check has fetched metadata')
                self.assertTrue(worker._repo_lock('another').acquire(blocking=False),
                    'Unrelated clone lock remains available')
                worker._repo_lock('another').release()
                code, data, _ = self.post('check-windows')
                self.assertEqual(409,code)
                self.assertEqual('REPOSITORY_OPERATION_PENDING',data['error'])
                code, data, _ = self.post('pull-execute','{"previewId":"x","confirm":"PULL_WINDOWS_MAIN"}')
                self.assertEqual(409,code)
                self.assertEqual('REPOSITORY_OPERATION_PENDING',data['error'])
                # Other clone is not blocked by this lock; fixture does not
                # register it, so the expected outcome is a different denial.
                code, data, _ = self.post('check-windows',repo='another')
                self.assertEqual(409,code)
                self.assertEqual('DOCKER_REPOSITORY_NOT_REGISTERED',data['error'])
            finally:
                release.set()
            code, _, _ = pending.result(timeout=3)
            self.assertEqual(200,code)
        code, _, _ = self.post('check-windows')
        self.assertEqual(200,code,'Server releases the clone guard on completion')

    def test_owner_check_is_forbidden_in_read_only_preview_and_without_worker(self):
        self.login()
        self.server.allow_browser_pull=False
        self.assertEqual(405,self.post('check-windows')[0])
        self.assertEqual([],self.mirror.checks)
        self.server.allow_browser_pull=True
        with mock.patch.object(BrowserPull, 'configured', new_callable=mock.PropertyMock,
                               return_value=False):
            self.assertEqual(405,self.post('check-windows')[0])
        self.assertEqual([],self.mirror.checks)

    def test_invalid_json_routes_and_failed_session_reuse(self):
        self.login()
        for path, body in [('pull-preview', '{'), ('pull-preview', '{"key":1,"key":2}'),
                           ('pull-preview', '{"x":1}'), ('pull-execute', '{}')]:
            with self.subTest(path=path, body=body):
                code, _, _ = self.post(path, body)
                self.assertEqual(400 if body in ('{', '{"key":1,"key":2}') else 409, code)
        code, _, _ = self.post('unexpected-route')
        self.assertEqual(405, code)
        self.assertEqual(405, self.request('PUT', '/api/v1/repositories/sample', '{}')[0])
        self.assertEqual([], self.mirror.calls)

    def test_old_source_preview_mode_stays_strictly_read_only(self):
        self.server.allow_browser_pull = False
        self.login()
        code, caps, _ = self.request('GET', '/api/v1/capabilities', headers={'Cookie': self.cookie})
        self.assertEqual(200, code)
        self.assertTrue(caps['readOnly'])
        self.assertFalse(caps['browserPull']['configured'])
        code, value, _ = self.post('pull-preview')
        self.assertEqual(405, code)
        self.assertEqual('BROWSER_PULL_PREVIEW_MODE_DISABLED', value['error'])
        self.assertEqual([], self.mirror.calls)

    def test_preview_execute_result_and_one_time_confirmation(self):
        self.login()
        code, data, _ = self.post('pull-preview')
        self.assertEqual(200, code)
        self.assertTrue(data['available'])
        preview, request_id = data['previewId'], data['requestId']
        wrong = {'previewId': preview, 'confirm': 'yes'}
        code, _, _ = self.post('pull-execute', json.dumps(wrong))
        self.assertEqual(409, code)
        valid = {'previewId': preview, 'confirm': 'PULL_WINDOWS_MAIN'}
        fake = {'requestId': request_id, 'head': NEW, 'state': 'EXECUTED', 'checkedAt': 'now'}
        with mock.patch.object(self.server.browser_pull.worker, '_execute', return_value=fake) as called:
            code, outcome, _ = self.post('pull-execute', json.dumps(valid))
            self.assertEqual(200, code)
            self.assertEqual(fake, outcome)
            code, _, _ = self.post('pull-execute', json.dumps(valid))
            self.assertEqual(409, code)
            called.assert_called_once()
        code, receipt, _ = self.request('GET', f'/api/v1/repositories/sample/pull-results/{request_id}',
                                        headers={'Cookie': self.cookie})
        self.assertEqual(200, code)
        self.assertEqual(fake, receipt)
        code, _, _ = self.request('GET', f'/api/v1/repositories/other/pull-results/{request_id}',
                                  headers={'Cookie': self.cookie})
        self.assertEqual(404, code)
        code, _, _ = self.request('GET', f'/api/v1/repositories/sample/pull-results/{request_id}')
        self.assertEqual(401, code)
        # Another authenticated owner session cannot hijack an old preview/receipt.
        previous_cookie, previous_csrf = self.cookie, self.csrf
        self.login()
        code, _, _ = self.post('pull-execute', json.dumps(valid))
        self.assertEqual(409, code)
        code, _, _ = self.request('GET', f'/api/v1/repositories/sample/pull-results/{request_id}',
                                  headers={'Cookie': self.cookie})
        self.assertEqual(404, code)
        self.cookie, self.csrf = previous_cookie, previous_csrf


if __name__ == '__main__':
    unittest.main()
