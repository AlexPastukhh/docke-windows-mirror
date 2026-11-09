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
        self.server = MirrorServer(('127.0.0.1', 0), self.mirror)
        self.server.browser_pull = BrowserPull(self.mirror, Path(tmp.name) / 'runtime.sqlite')
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
