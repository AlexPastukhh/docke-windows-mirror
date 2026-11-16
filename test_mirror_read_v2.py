"""Offline v2 domain/ingress tests; no real clones, services, credentials or effects."""
import copy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from mirror_api.core import MirrorCore
from mirror_api.gateway_v2 import ReadV2Handler, route
from mirror_api.read_v2 import CoreReadV2, READS, DEFERRED, ReadError, scope_digest

AT = '2026-11-17T01:00:00+00:00'
HEAD = 'a' * 40
PARAMS = {'offset': 0, 'limit': 32, 'inventoryRevision': None}


def row(repo='fortis-approvals'):
    primary = {'repoId': repo, 'kind': 'primary', 'branch': 'main', 'head': HEAD,
               'clean': True, 'status': 'registered', 'targetEligible': True,
               'origin': 'secret-origin', 'path': 'private-path', 'changedFiles': ['private-file']}
    tasks = [dict(primary, kind='worktree', task='task-' + str(i), branch='task/' + str(i)) for i in range(3)]
    return {'repoId': repo, 'docker': {'primary': primary, 'worktrees': [primary] + tasks},
            'windows': {'repoId': repo, 'checkedAt': AT, 'head': HEAD, 'branch': 'main', 'clean': True,
                        'error': None, 'origin': 'private-origin', 'exists': True},
            'github': {'repoId': repo, 'checkedAt': None, 'head': None, 'error': 'NOT_CHECKED'},
            'actions': {'pull_main': {'available': True}}}


class Backend:
    windows_root = Path('/unused-fixture')

    def __init__(self):
        self.value, self.calls = row(), []

    def inventory(self, remote=False, repo=None, *, details=False):
        self.calls.append((remote, repo, details))
        value = copy.deepcopy(self.value)
        if remote:
            value['github'] = {'repoId': repo, 'checkedAt': AT, 'head': HEAD, 'error': None,
                'readErrors': {}, 'truncated': {}, 'pullRequests': [
                    {'number': 12, 'head': {'sha': HEAD}, 'base': {'sha': HEAD},
                     'state': 'closed', 'merged': True, 'mergeVerified': True, 'draft': False}],
                'workflowRuns': [{'id': i, 'headSha': HEAD, 'status': 'completed', 'conclusion': 'success'}
                                 for i in (30, 20, 10)]}
        return {'schemaVersion': 1, 'checkedAt': AT, 'repositories': [value], 'errors': []}


class ReadV2Tests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.core = MirrorCore(self.backend, read_only=True)
        self.addCleanup(self.core.close)
        self.api = CoreReadV2(self.core, allowed_repos=['fortis-approvals'],
                             inventories={'mirror-pilot': ['fortis-approvals']})

    def call(self, op, target='fortis-approvals', params=None, **kwargs):
        return self.api.call(op, target, {} if params is None else params, **kwargs)

    def test_projection_and_unknowns_never_claim_equality_or_effect_availability(self):
        result = self.call('get_repository_v2')
        self.assertIsNone(result['error'])
        data = result['data']
        self.assertEqual('VERIFIED', data['windows']['verification'])
        self.assertEqual('NOT_CHECKED', data['github']['verification'])
        self.assertEqual('unknown', data['relation'])
        self.assertIsNone(data['github']['head'])
        text = json.dumps(result)
        for private in ('origin', 'path', 'changedFiles', 'private-', 'secret-'):
            self.assertNotIn(private, text)
        self.assertFalse(any(a['available'] for a in data['actions'] if a['operation'] in DEFERRED))
        self.assertEqual([(False, 'fortis-approvals', False)], self.backend.calls)
        self.assertIsNone(self.core._worker)
        self.assertIsNone(self.core._guard)
        self.backend.value['windows'].update(head=None, clean=None)
        data = self.call('get_repository_v2')['data']
        self.assertEqual('UNKNOWN', data['windows']['verification'])
        self.assertEqual('unknown', data['relation'])

    def test_named_inventory_is_finite_and_digest_must_match_before_core_work(self):
        result = self.call('list_repositories_v2', 'mirror-pilot', PARAMS,
                           inventory_scope=scope_digest(['fortis-approvals']))
        self.assertEqual(['fortis-approvals'], result['data']['items'])
        for scope in (None, scope_digest(['fortis-approvals', 'newly-discovered'])):
            data = self.call('list_repositories_v2', 'mirror-pilot', PARAMS, inventory_scope=scope)
            self.assertEqual('REPOSITORY_NOT_ALLOWED', data['error']['code'])
        self.assertEqual([], self.backend.calls)

    def test_worktree_pages_exclude_primary_and_reject_changed_revision(self):
        params = dict(PARAMS, limit=1)
        first = self.call('list_worktrees_v2', params=params)['data']
        self.assertEqual('task-0', first['items'][0]['task'])
        second = self.call('list_worktrees_v2', params=dict(params, offset=1,
                           inventoryRevision=first['inventoryRevision']))['data']
        self.assertEqual('task-1', second['items'][0]['task'])
        self.backend.value['docker']['worktrees'][2]['head'] = 'b' * 40
        stale = self.call('list_worktrees_v2', params=dict(params, offset=1,
                          inventoryRevision=first['inventoryRevision']))
        self.assertEqual('INVENTORY_CHANGED', stale['error']['code'])
        self.assertIsNone(stale['data'])

    def test_missing_duplicate_primary_duplicate_task_and_over_limit_fail_closed(self):
        for mode in ('missing-primary', 'duplicate-primary', 'duplicate-task', 'too-many'):
            with self.subTest(mode=mode):
                self.backend.value = row()
                docker = self.backend.value['docker']
                if mode == 'missing-primary': docker['worktrees'].pop(0)
                elif mode == 'duplicate-primary': docker['worktrees'].append(docker['primary'])
                elif mode == 'duplicate-task': docker['worktrees'].append(docker['worktrees'][1])
                else: docker['worktrees'] *= 40
                self.assertIsNotNone(self.call('list_worktrees_v2', params=PARAMS)['error'])

    def test_github_independent_lists_page_without_fabricating_required_ci(self):
        first = self.call('get_github_status_v2', params=dict(PARAMS, limit=1))['data']
        self.assertEqual('MERGED', first['pullRequests'][0]['state'])
        self.assertEqual(10, first['runs'][0]['id'])
        second = self.call('get_github_status_v2', params=dict(PARAMS, limit=1, offset=1,
                           inventoryRevision=first['inventoryRevision']))['data']
        self.assertEqual([], second['pullRequests'])
        self.assertEqual(20, second['runs'][0]['id'])
        self.assertEqual('NOT_VERIFIED', second['requiredCI'])
        self.assertEqual([(True, 'fortis-approvals', True)] * 2, self.backend.calls)
        self.assertIsNone(self.core._worker)

    def test_github_partial_errors_and_unverified_closed_pr_are_unavailable(self):
        original = self.backend.inventory
        for mode in ('read-error', 'duplicate-run', 'null-head'):
            def broken(*args, **kwargs):
                result = original(*args, **kwargs)
                raw = result['repositories'][0]['github']
                if mode == 'read-error': raw['readErrors'] = {'pullRequests': 'secret-error'}
                elif mode == 'duplicate-run': raw['workflowRuns'].append(raw['workflowRuns'][0])
                else: raw['head'] = None
                return result
            with self.subTest(mode=mode), patch.object(self.backend, 'inventory', side_effect=broken):
                result = self.call('get_github_status_v2', params=PARAMS)
                self.assertIsNone(result['data'])
                self.assertNotIn('secret-error', json.dumps(result))

    def test_unverified_closed_pr_is_explicitly_partial_without_invented_closed_state(self):
        original = self.backend.inventory
        def partial(*args, **kwargs):
            result = original(*args, **kwargs)
            result['repositories'][0]['github']['pullRequests'][0]['mergeVerified'] = False
            return result
        with patch.object(self.backend, 'inventory', side_effect=partial):
            data = self.call('get_github_status_v2', params=PARAMS)['data']
        self.assertEqual([], data['pullRequests'])
        self.assertTrue(data['truncated'])

    def test_bad_params_unknown_scope_and_deferred_ops_do_not_touch_core(self):
        for params in ({}, dict(PARAMS, offset=True), dict(PARAMS, limit=129),
                       dict(PARAMS, offset=1), dict(PARAMS, actor='spoof')):
            self.assertIsNotNone(self.call('list_worktrees_v2', params=params)['error'])
        self.assertIsNotNone(self.call('get_repository_v2', 'another')['error'])
        for operation in DEFERRED:
            self.assertEqual('POLICY_DENIED', self.call(operation)['error']['code'])
        self.assertEqual([], self.backend.calls)
        with self.assertRaises(ReadError):
            CoreReadV2(self.core, allowed_repos=['fortis-approvals'], inventories={'bad': ['another']})

    def test_malformed_backend_containers_and_cross_repo_sources_fail_closed(self):
        for field in ('docker', 'windows', 'github'):
            with self.subTest(field=field):
                self.backend.value = row()
                self.backend.value[field] = ['invalid-container']
                self.assertIsNotNone(self.call('get_repository_v2')['error'])
        self.backend.value = row()
        self.backend.value['docker']['primary']['repoId'] = 'other'
        self.assertIsNotNone(self.call('get_repository_v2')['error'])

    def test_core_closure_and_raw_failures_are_fixed_errors(self):
        with patch.object(self.backend, 'inventory', side_effect=OSError('sensitive-output')):
            result = self.call('get_repository_v2')
        self.assertEqual({'code': 'SOURCE_UNAVAILABLE', 'retryable': False}, result['error'])
        self.core.close()
        self.assertIsNotNone(self.call('get_repository_v2')['error'])

    def test_catalog_and_shared_provider_fixtures_match_this_source(self):
        root = Path(__file__).parent / 'docs' / 'unified-api'
        catalog = json.loads((root / 'contract.json').read_text())
        self.assertFalse(catalog['runtimeActivation'])
        self.assertEqual(set(READS), {op['operation'] for op in catalog['operations'] if op['implemented']})
        self.assertTrue(all(op['defaultPolicy'] == 'DENY' for op in catalog['operations']))
        fixtures = json.loads((root / 'read-v2-fixtures.json').read_text())
        with patch('mirror_api.read_v2.checked_at', return_value=AT):
            for fixture in fixtures[:6]:
                operation = fixture['value']['operation']
                named = operation in ('list_repositories_v2', 'get_capabilities_v2')
                params = PARAMS if operation in ('list_repositories_v2', 'list_worktrees_v2', 'get_github_status_v2') else {}
                actual = self.call(operation, 'mirror-pilot' if named else 'fortis-approvals', params,
                                   inventory_scope=scope_digest(['fortis-approvals']) if named else None)
                self.assertEqual(fixture['value'], actual)


class IngressTests(unittest.TestCase):
    def setUp(self):
        ReadV2Tests.setUp(self)
        server = self.server = ThreadingHTTPServer(('127.0.0.1', 0), ReadV2Handler)
        server.gateway_secret, server.allowed_repos = 'a' * 48, self.api.allowed_repos
        server.mirror, server.read_v2 = self.core, self.api
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)

    def request(self, path, *, method='GET', headers=()):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader('Host', 'host.docker.internal')
        conn.putheader('Authorization', 'Bearer ' + 'a' * 48)
        for key, value in headers: conn.putheader(key, value)
        conn.endheaders()
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_v1_compatibility_and_v2_use_same_core_without_worker(self):
        status, value = self.request('/gateway/v1/repositories/fortis-approvals')
        self.assertEqual(200, status)
        self.assertEqual(1, value['schemaVersion'])
        status, value = self.request('/gateway/v2/repositories/fortis-approvals')
        self.assertEqual(200, status)
        self.assertEqual(2, value['schemaVersion'])
        self.assertIsNone(self.core._worker)

    def test_named_inventory_wire_and_policy_denial(self):
        query = urlencode({'inventory': 'mirror-pilot', 'scope_digest': scope_digest(['fortis-approvals']),
                           'offset': 0, 'limit': 32})
        status, value = self.request('/gateway/v2/repositories?' + query)
        self.assertEqual(200, status)
        self.assertEqual(['fortis-approvals'], value['data']['items'])
        status, value = self.request('/gateway/v2/repositories/fortis-approvals', method='POST')
        self.assertEqual(405, status)
        self.assertEqual([], self.backend.calls)

    def test_ambiguous_headers_browser_identity_and_get_bodies_fail_before_core(self):
        for headers in ((('Host', 'host.docker.internal'),), (('Authorization', 'Bearer ' + 'a'*48),),
                        (('Cookie', 'mirror_session=owner'),), (('Origin', 'http://127.0.0.1'),),
                        (('X-CSRF-Token', 'spoof'),), (('Content-Length', '2'),),
                        (('Transfer-Encoding', 'chunked'),)):
            with self.subTest(headers=headers):
                self.assertIn(self.request('/gateway/v2/repositories/fortis-approvals', headers=headers)[0], (401, 403))
        self.assertEqual([], self.backend.calls)

    def test_route_normalization_queries_and_unbound_lookup_are_denied(self):
        for path in ('/gateway/v2/repositories/%66ortis-approvals',
                     '/gateway/v2/repositories/fortis-approvals/../other',
                     '/gateway/v2/repositories/fortis-approvals?remote=true',
                     '/gateway/v2/repositories/fortis-approvals/worktrees?offset=0&offset=1&limit=1',
                     '/gateway/v2/repositories/fortis-approvals/worktrees?offset=-1&limit=1',
                     '/gateway/v2/repositories/fortis-approvals/pull-preview',
                     '/gateway/v2/repositories/fortis-approvals/pull-results/47eeca45-5f85-402b-be58-6e4261381239'):
            with self.subTest(path=path): self.assertEqual(400, self.request(path)[0])
        self.assertEqual([], self.backend.calls)


if __name__ == '__main__':
    unittest.main()
