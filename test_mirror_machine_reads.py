"""Durable identity, expiry/restart and private receipt projections; fixture roots only."""
import copy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from mirror_api.core import MirrorCore
from mirror_api.gateway_v2 import ReadV2Handler
from mirror_api.machine_reads import MachineReads
from mirror_api.read_v2 import CoreReadV2, ReadError
from test_mirror_read_v2 import Backend, AT

OLD, NEW = 'b' * 40, 'a' * 40
CREATE = '0ec61e55-b834-47b7-8e6c-b8027553a4bf'
EFFECT = '2166d435-e811-41d3-a8c8-c7d807942c25'
ORIGIN = 'https://github.com/AlexPastukhh/fortis-approvals.git'


class BoundBackend(Backend):
    def inventory(self, *args, **kwargs):
        result = super().inventory(*args, **kwargs)
        row = result['repositories'][0]
        row['docker']['primary']['origin'] = ORIGIN
        row['windows'].update(head=OLD, origin=ORIGIN, originValid=True, relation='unknown')
        row['github']['githubRepository'] = 'fortis-approvals'
        return result


class MachineReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = BoundBackend(); self.backend.windows_root = self.root / 'Repos'
        self.backend.windows_root.mkdir()
        self.core = MirrorCore(self.backend, self.root / 'receipts.sqlite')
        self.addCleanup(self.core.close)
        self.api = CoreReadV2(self.core, allowed_repos=['fortis-approvals'],
                             inventories={'mirror-pilot': ['fortis-approvals']}, machine_generation='c' * 64)
        self.machine = self.api._bound_reads()
        self.ticks = [1794877200]
        self.machine.clock = lambda: self.ticks[0]

    def preview(self, *, actor='repo:fixture', request_id=CREATE, generation='d' * 64):
        return self.api.call('preview_windows_pull_v2', 'fortis-approvals', {},
                             principal=actor, request_id=request_id, gateway_generation=generation)

    def lookup(self, actor='repo:fixture', request_id=EFFECT):
        return self.api.call('get_windows_pull_result_v2', 'fortis-approvals',
                             {'requestId': request_id}, principal=actor)

    def seed_binding(self, state='EXECUTED', result=None, payload=None):
        preview = self.preview()['data']
        self.assertEqual([], preview['blockers'])
        identity = {k: preview[k] for k in ('previewId', 'expectedHead', 'targetSha', 'configGeneration')}
        payload = payload or dict(requestId=EFFECT, repoId='fortis-approvals', expectedHead=OLD, targetSha=NEW, origin=ORIGIN)
        result = result or dict(requestId=EFFECT, state=state, head=NEW)
        with self.machine.worker._db() as db:
            # Only fixture seeding: production has no binding writer before P4.
            db.execute('INSERT INTO machine_bindings VALUES(?,?,?,?)',
                       (EFFECT, 'repo:fixture', 'fortis-approvals', json.dumps(identity)))
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (EFFECT, json.dumps(payload), state, json.dumps(result)))
        return preview

    def test_preview_is_frozen_across_expiry_and_restart_without_writer_or_recovery(self):
        with patch.object(self.machine.worker.guard, 'acquire') as writer, \
             patch.object(self.machine.worker, 'status') as status, \
             patch.object(self.machine.worker, 'run') as run:
            first = self.preview()['data']
            self.assertEqual([], first['blockers'])
            self.assertIsNotNone(first['previewId'])
            self.ticks[0] += 500
            self.backend.value['docker']['primary']['head'] = 'f' * 40
            self.assertEqual(first, self.preview()['data'])
            self.assertEqual(1, len(self.backend.calls))
            self.assertEqual(first, MachineReads(self.core, self.api.machine_generation, clock=lambda: self.ticks[0]).preview(
                'fortis-approvals', 'repo:fixture', CREATE, 'd' * 64))
            writer.assert_not_called(); status.assert_not_called(); run.assert_not_called()
        self.assertNotIn(ORIGIN, json.dumps(first))
        self.core.close()
        reopened = MirrorCore(self.backend, self.root / 'receipts.sqlite')
        self.addCleanup(reopened.close)
        self.assertEqual(first, MachineReads(reopened, self.api.machine_generation).preview(
            'fortis-approvals', 'repo:fixture', CREATE, 'd' * 64))
        self.assertEqual(1, len(self.backend.calls))

    def test_cross_actor_uuid_reuse_and_generation_rotation_never_refresh_preview(self):
        first = self.preview()['data']
        self.assertEqual('REQUEST_ID_CONFLICT', self.preview(actor='another')['error']['code'])
        self.assertEqual('PREVIEW_STALE', self.preview(generation='e' * 64)['error']['code'])
        with self.machine.worker._db() as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM machine_previews').fetchone()[0])
        self.assertEqual(first, self.preview()['data'])

    def test_private_preview_id_lookup_is_frozen_and_uses_no_writer_or_git(self):
        first = self.preview()['data']
        self.assertEqual([], first['blockers'])
        with patch.object(self.core, 'preview_observation') as observe, \
             patch.object(self.machine.worker, 'run') as run, \
             patch.object(self.machine.worker, 'status') as status, \
             patch.object(self.machine.worker.guard, 'acquire') as writer:
            saved = self.machine.persisted_preview_for_ask(
                'fortis-approvals', 'repo:fixture', first['previewId'], 'd' * 64)
            self.assertEqual({'readRequestId': CREATE, 'origin': ORIGIN, 'snapshot': first}, saved)
            self.backend.value['docker']['primary']['head'] = 'f' * 40
            self.assertEqual(saved, self.machine.persisted_preview_for_ask(
                'fortis-approvals', 'repo:fixture', first['previewId'], 'd' * 64))
            observe.assert_not_called(); run.assert_not_called()
            status.assert_not_called(); writer.assert_not_called()

    def test_private_preview_id_lookup_denies_foreign_and_unknown_id(self):
        first = self.preview()['data']
        for actor, repo, ident in [
            ('another', 'fortis-approvals', first['previewId']),
            ('repo:fixture', 'other-repo', first['previewId']),
            ('repo:fixture', 'fortis-approvals', EFFECT)]:
            with self.subTest(actor=actor, repo=repo):
                with self.assertRaisesRegex(ReadError, '^PERMISSION_DENIED$'):
                    self.machine.persisted_preview_for_ask(repo, actor, ident, 'd' * 64)
        self.assertEqual('PERMISSION_DENIED', self.lookup(request_id=CREATE)['error']['code'])

    def test_private_preview_id_lookup_generation_expiry_and_restart(self):
        first = self.preview()['data']
        with self.assertRaisesRegex(ReadError, '^PREVIEW_STALE$'):
            self.machine.persisted_preview_for_ask(
                'fortis-approvals', 'repo:fixture', first['previewId'], 'e' * 64)
        reopened = MachineReads(self.core, self.api.machine_generation, clock=lambda: self.ticks[0])
        self.assertEqual(first, reopened.persisted_preview_for_ask(
            'fortis-approvals', 'repo:fixture', first['previewId'], 'd' * 64)['snapshot'])
        self.ticks[0] += 120
        with self.assertRaisesRegex(ReadError, '^PREVIEW_EXPIRED$'):
            reopened.persisted_preview_for_ask(
                'fortis-approvals', 'repo:fixture', first['previewId'], 'd' * 64)
        # Historic UUID READ is still frozen, not renewed for ASK.
        self.assertEqual(first, self.preview()['data'])

    def test_private_preview_id_lookup_rejects_durable_identity_mismatch(self):
        first = self.preview()['data']
        with self.machine.worker._db() as db:
            data = dict(first, targetSha='f' * 40)
            db.execute('UPDATE machine_previews SET data=? WHERE preview_id=?',
                       (json.dumps(data), first['previewId']))
        with self.assertRaisesRegex(ReadError, '^REQUEST_ID_CONFLICT$'):
            self.machine.persisted_preview_for_ask(
                'fortis-approvals', 'repo:fixture', first['previewId'], 'd' * 64)

    def test_capacity_fails_closed_and_does_not_renew_existing_ids(self):
        first = self.preview()['data']
        self.machine.MAX_PREVIEWS = 1
        fresh = '0ec61e55-b834-47b7-8e6c-b8027553a4be'
        self.assertEqual('LIMIT_EXCEEDED', self.preview(request_id=fresh)['error']['code'])
        self.assertEqual(first, self.preview()['data'])

    def test_blocked_preview_never_allocates_executable_id(self):
        self.backend.value['docker']['primary']['clean'] = False
        data = self.preview()['data']
        self.assertIsNone(data['previewId'])
        self.assertIn('DOCKER_MAIN_MISMATCH', data['blockers'])

    def test_unbound_browser_foreign_and_unknown_receipts_are_indistinguishable(self):
        with self.machine.worker._db() as db:
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (EFFECT, json.dumps({'repoId': 'fortis-approvals'}), 'EXECUTED', '{}'))
        with patch.object(self.machine.worker, 'status') as probe:
            expected = self.lookup()['error']
            self.assertEqual('PERMISSION_DENIED', expected['code'])
            self.assertEqual(expected, self.lookup('another')['error'])
            self.assertEqual(expected, self.lookup(request_id=CREATE)['error'])
            probe.assert_not_called()

    def test_valid_binding_projects_stored_outcome_and_survives_expired_preview(self):
        self.seed_binding()
        self.ticks[0] += 1000
        with patch.object(self.machine.worker, 'status') as probe:
            data = self.lookup()['data']
            probe.assert_not_called()
        self.assertEqual(('EXECUTED', 'CONFIRMED', NEW, 'WORKER_RECEIPT'),
                         (data['workerState'], data['effect'], data['observedHead'], data['evidence']))
        self.assertNotIn(ORIGIN, json.dumps(data))
        self.assertEqual('PERMISSION_DENIED', self.lookup('another')['error']['code'])

    def test_dispatched_unknown_and_missing_receipt_never_claim_no_effect(self):
        self.seed_binding(state='DISPATCHED')
        data = self.lookup()['data']
        self.assertEqual(('DISPATCHED', 'UNKNOWN'), (data['workerState'], data['effect']))
        with self.machine.worker._db() as db:
            db.execute('DELETE FROM pull_receipts WHERE request_id=?', (EFFECT,))
        data = self.lookup()['data']
        self.assertEqual(('NOT_FOUND', 'UNKNOWN'), (data['workerState'], data['effect']))

    def test_payload_conflict_and_unconfirmed_finish_do_not_complete_receipt(self):
        self.seed_binding(result=dict(requestId=EFFECT, state='EXECUTED', head=NEW, workerFinished=False))
        self.assertEqual('UNKNOWN', self.lookup()['data']['effect'])
        with self.machine.worker._db() as db:
            db.execute('UPDATE pull_receipts SET payload=?', ('{}',))
        self.assertEqual('REQUEST_ID_CONFLICT', self.lookup()['error']['code'])

    def test_simultaneous_same_uuid_produces_one_durable_preview(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2) as pool:
            data = list(pool.map(lambda _: self.preview()['data'], range(2)))
        self.assertEqual(data[0], data[1])
        with self.machine.worker._db() as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM machine_previews').fetchone()[0])

    def test_bound_wire_uses_canonical_internal_identity_and_denies_effect_routes(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), ReadV2Handler)
        server.gateway_secret, server.allowed_repos = 'a' * 48, self.api.allowed_repos
        server.mirror, server.read_v2 = self.core, self.api
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            query = urlencode({'principal': 'repo:fixture', 'request_id': CREATE, 'gateway_generation': 'd' * 64})
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            conn.request('GET', '/gateway/v2/repositories/fortis-approvals/pull-preview?' + query,
                         headers={'Host': 'host.docker.internal', 'Authorization': 'Bearer ' + 'a' * 48})
            response = conn.getresponse(); value = json.loads(response.read());conn.close()
            self.assertEqual(200, response.status)
            self.assertEqual([], value['data']['blockers'])
        finally:
            server.shutdown();thread.join(2);server.server_close()


if __name__ == '__main__':
    unittest.main()
