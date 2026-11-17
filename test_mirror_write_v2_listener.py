"""Offline ephemeral-loopback HTTP framing tests for UNREGISTERED P4 candidate.

No Windows TLS listener, production credentials, owner keys, Git or deployment.
Real transport admission is deliberately NOT mocked for negative TLS tests.
"""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import json
import ssl
import threading
import unittest
from types import SimpleNamespace

from mirror_api.gateway_v2 import ReadV2Handler, create_server as live_read_server
from mirror_api.write_v2_listener import PrivateWriteV2Handler
from mirror_api.read_v2 import ReadError
import test_mirror_write_v2_bridge as bridge_fixture

READ, WRITE, PATH = bridge_fixture.READ, bridge_fixture.WRITE, bridge_fixture.PATH
from test_mirror_atomic_reservation import ACTOR, EFFECT, REPO


class PrivateWriteListenerTests(unittest.TestCase):
    def _dispatch(self, request_id=EFFECT):
        return bridge_fixture.PrivateWriteBridgeTests._dispatch(self, request_id)

    encode = staticmethod(bridge_fixture.PrivateWriteBridgeTests.encode)

    def setUp(self):
        bridge_fixture.PrivateWriteBridgeTests.setUp(self)
        server = self.server = ThreadingHTTPServer(('127.0.0.1', 0), PrivateWriteV2Handler)
        server.daemon_threads = True
        server.owner_write_opt_in = False
        server.private_write_bridge = self.bridge
        server.owner_write_peer_gate = lambda _cert, _peer: True
        server.gateway_secret = READ
        server.allowed_repos = self.reads.allowed_repos
        server.read_v2 = self.reads
        server.mirror = self.core
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)

    def request(self, *, method='POST', path=PATH, body=None, headers=None):
        raw = self.body if body is None else body
        if headers is None:
            headers = [('Host', 'host.docker.internal:443'),
                       ('Authorization', 'Bearer ' + WRITE),
                       ('Content-Type', 'application/json'),
                       ('Content-Length', str(len(raw)))]
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key,value in headers:
                conn.putheader(key,value)
            conn.endheaders(raw)
            response = conn.getresponse()
            result = response.status,json.loads(response.read().decode('ascii'))
            return result
        finally:
            conn.close()

    def count_bindings(self):
        with self.machine.worker._db() as db:
            return db.execute('SELECT COUNT(*) FROM machine_bindings').fetchone()[0]

    def test_default_disabled_returns_405_without_parsing_or_touching_core(self):
        with patch.object(self.bridge, 'process_envelope') as ingress, \
             patch.object(self.machine.worker, '_execute') as effect:
            status,reply=self.request()
            self.assertEqual((status,reply['error']), (405,'GATEWAY_READ_ONLY'))
            ingress.assert_not_called()
            effect.assert_not_called()
        self.assertEqual(self.count_bindings(),0)

    def test_shipping_factory_still_registers_only_read_handler(self):
        server=SimpleNamespace()
        with patch('mirror_api.gateway_v2.v1_server',return_value=server):
            actual=live_read_server(self.core,bind='127.0.0.1',cert='unused',
                                    key='unused',secret_file='unused',
                                    allowed_repos=[REPO],
                                    inventories={'mirror-pilot':[REPO]},
                                    machine_generation='c'*64)
        self.assertIs(actual.RequestHandlerClass, ReadV2Handler)
        self.assertFalse(hasattr(actual,'owner_write_opt_in'))
        self.assertFalse(hasattr(actual,'private_write_bridge'))

    def test_no_tls_or_verified_source_refuses_before_receiving_body(self):
        self.server.owner_write_opt_in=True
        with patch.object(self.bridge,'process_envelope') as ingress:
            status,value=self.request()
        self.assertEqual((status,value['error']), (403,'GATEWAY_WRITE_TRANSPORT_DENIED'))
        ingress.assert_not_called()
        self.assertEqual(self.count_bindings(),0)

    def test_peer_gate_requires_real_peer_cert_and_exact_trust(self):
        class FakeSSL:
            context=SimpleNamespace(verify_mode=ssl.CERT_REQUIRED)
            def version(self):
                return 'TLSv1.3'
            def getpeercert(self, *, binary_form):
                assert binary_form is True
                return b'ephemeral-verified-client-cert'
        handler=object.__new__(PrivateWriteV2Handler)
        handler.connection=FakeSSL()
        handler.client_address=('192.0.2.100',53000)
        seen=[]
        handler.server=SimpleNamespace(
            owner_write_peer_gate=lambda cert, peer: (
                seen.append((cert,peer)) or 1))
        with patch('mirror_api.write_v2_listener.ssl.SSLSocket',FakeSSL):
            self.assertFalse(handler._transport_allowed())  # truthy is not True
            handler.server.owner_write_peer_gate=lambda _cert, _peer: (
                (_ for _ in ()).throw(OSError('peer unavailable')))
            self.assertFalse(handler._transport_allowed())
            handler.server.owner_write_peer_gate=lambda cert,peer: (
                cert == b'ephemeral-verified-client-cert' and
                peer == ('192.0.2.100',53000))
            self.assertTrue(handler._transport_allowed())
            self.assertEqual(seen,[(
                b'ephemeral-verified-client-cert',('192.0.2.100',53000))])
            handler.connection.context=SimpleNamespace(verify_mode=ssl.CERT_OPTIONAL)
            self.assertFalse(handler._transport_allowed())
            handler.connection.context=SimpleNamespace(verify_mode=ssl.CERT_REQUIRED)
            handler.connection.getpeercert=lambda **kw: None
            self.assertFalse(handler._transport_allowed())
            handler.connection.getpeercert=lambda **kw: b''
            self.assertFalse(handler._transport_allowed())
            handler.connection.getpeercert=lambda **kw: b'ephemeral-verified-client-cert'
            handler.connection.version=lambda: 'TLSv1.1'
            self.assertFalse(handler._transport_allowed())
        self.assertEqual(self.count_bindings(),0)

    def test_tls_and_owner_opt_in_required_before_reading_even_oversized_body(self):
        self.server.owner_write_opt_in=True
        self.server.private_write_bridge=None
        with patch.object(PrivateWriteV2Handler,'_transport_allowed',return_value=True), \
             patch.object(self.bridge,'process_envelope') as ingress:
            status,value=self.request(body=b'',headers=[
                ('Host','host.docker.internal:443'),
                ('Content-Length','9999')])
        self.assertEqual((status,value['error']), (405,'GATEWAY_READ_ONLY'))
        ingress.assert_not_called()

    def test_oversize_and_ambiguous_framing_refuses_before_read(self):
        self.server.owner_write_opt_in=True
        for name, headers in (
            ('oversize',[('Content-Length','4097')]),
            ('duplicate-length',[('Content-Length','20'),('content-length','20')]),
            ('no-length',[]),
            ('chunked',[('Content-Length','20'),('Transfer-Encoding','chunked')]),
            ('expect',[('Content-Length','20'),('Expect','100-continue')]),
            ('encoded',[('Content-Length','20'),('Content-Encoding','gzip')]),
        ):
            final = [('Host','host.docker.internal:443'),('Authorization','Bearer '+WRITE),
                     ('Content-Type','application/json')]+headers
            with self.subTest(name=name), \
                 patch.object(PrivateWriteV2Handler,'_transport_allowed',return_value=True), \
                 patch.object(self.bridge,'process_envelope') as effect:
                status,value=self.request(body=b'',headers=final)
                self.assertIn(status,(400,413))
                effect.assert_not_called()
        self.assertEqual(self.count_bindings(),0)

    def test_safe_raw_envelope_reaches_one_shot_worker_only_under_explicit_fixture_gate(self):
        # Explicit patch of the transport gate is *only* on an isolated
        # ephemeral non-TLS loopback fixture. The production gate still
        # requires an actual SSLSocket + reviewed owner peer verifier.
        self.server.owner_write_opt_in=True
        with patch.object(PrivateWriteV2Handler,'_transport_allowed',return_value=True), \
             patch('mirror_api.proof_v2._verify_ed25519') as proof, \
             patch.object(self.machine.worker,'_execute',
                          return_value={'requestId':EFFECT,'state':'EXECUTED',
                                        'head':self.dispatch['targetSha']}) as effect:
            status,result=self.request()
            self.assertEqual(status,200)
            self.assertEqual((result['workerState'],result['effect']),('EXECUTED','CONFIRMED'))
            second,receipt=self.request()
            self.assertEqual((second,receipt['effect']),(200,'CONFIRMED'))
            self.assertEqual(proof.call_count,1)
            self.assertEqual(effect.call_count,1)
        self.assertEqual(self.count_bindings(),1)

    def test_duplicated_auth_wrong_read_bearer_cookie_and_bad_path(self):
        self.server.owner_write_opt_in=True
        variations=[
            ('wrong-bearer', PATH, [('Authorization','Bearer '+READ)]),
            ('duplicate-authorization',PATH,
                [('Authorization','Bearer '+WRITE),('authorization','Bearer '+WRITE)]),
            ('cookie',PATH,[('Authorization','Bearer '+WRITE),('Cookie','session=owner')]),
            ('bad-path',PATH+'?retry=true',[('Authorization','Bearer '+WRITE)]),
        ]
        for name,path,extra in variations:
            hdr=[('Host','host.docker.internal:443'),('Content-Type','application/json'),
                 ('Content-Length',str(len(self.body)))]+extra
            with self.subTest(name=name), \
                 patch.object(PrivateWriteV2Handler,'_transport_allowed',return_value=True), \
                 patch.object(self.machine.worker,'_execute') as effect:
                status,value=self.request(path=path,headers=hdr)
                self.assertIn(status,(400,403))
                effect.assert_not_called()
        self.assertEqual(self.count_bindings(),0)

    def test_unexpected_failure_does_not_claim_known_no_effect_or_retry(self):
        self.server.owner_write_opt_in=True
        with patch.object(PrivateWriteV2Handler,'_transport_allowed',return_value=True), \
             patch.object(self.bridge,'process_envelope',
                          side_effect=RuntimeError('private exception / actor')) as bridge:
            status,reply=self.request()
        self.assertEqual((status,reply['error']), (503,'OUTCOME_UNCONFIRMED'))
        self.assertNotIn('actor',json.dumps(reply))
        self.assertEqual(bridge.call_count,1)

    def test_ordinary_get_remains_read_only_and_read_bearer_scoped(self):
        # This READ fixture uses a separate principal/Preview read API.
        status,result=self.request(method='GET',
                                   path=f'/gateway/v2/repositories/{REPO}',
                                   body=b'',headers=[
                                       ('Host','host.docker.internal'),
                                       ('Authorization','Bearer '+READ)])
        self.assertEqual(status,200)
        self.assertEqual(result['operation'],'get_repository_v2')
