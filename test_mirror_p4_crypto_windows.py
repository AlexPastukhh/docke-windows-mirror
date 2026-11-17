"""P4 real Ed25519/SQLite boundary tests for hosted Windows CI, NEVER live Windows.

Only temporary fake repositories and an ephemeral in-memory test key are used.
The actual Git child is mocked. The shipping listener stays GET-only / POST 405.
A cryptography-free source checkout skips these tests, while Windows CI installs
the pinned verifier package and requires this module to actually run.
"""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mirror_api.core import MirrorCore
from mirror_api.proof_v2 import DOMAIN, IDENTITY, PinnedEd25519Keys, _canonical
from mirror_api.read_v2 import CoreReadV2, ReadError
from mirror_api.write_v2_bridge import PrivateWriteBridge
from mirror_api.write_v2_ingress import WriteIngressProfile
from test_mirror_machine_reads import BoundBackend, CREATE, EFFECT
from test_mirror_atomic_reservation import ACTOR, GEN, REPO, TIME
from test_mirror_write_v2_bridge import READ, WRITE, PATH


@unittest.skipUnless(importlib.util.find_spec('cryptography'),
                     'P4 real cryptography is required in hosted Windows CI')
class HostedWindowsP4CryptoTests(unittest.TestCase):
    def setUp(self):
        # Import lazily: the regular Linux/offline suite remains usable even
        # when this optional Windows CI-only package is not available.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        backend = BoundBackend()
        backend.windows_root = root / 'Repos'
        backend.windows_root.mkdir()
        self.core = MirrorCore(backend, root / 'receipt.sqlite')
        self.addCleanup(self.core.close)
        # Ephemeral test key only: not provisioned, saved, exported or reused
        # as any real Gateway/Windows owner signing credential.
        self.signer = Ed25519PrivateKey.generate()
        public = self.signer.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.pins = PinnedEd25519Keys({'fixture': public}, owner_epoch='e' * 64)
        self.reads = CoreReadV2(self.core, allowed_repos=[REPO],
                                inventories={'mirror-pilot': [REPO]},
                                machine_generation='c' * 64, owner_signing_pins=self.pins)
        self.machine = self.reads._bound_reads()
        self.ticks = [TIME]
        self.machine.clock = lambda: self.ticks[0]
        created = self.reads.call('preview_windows_pull_v2', REPO, {}, principal=ACTOR,
                                  request_id=CREATE, gateway_generation=GEN)
        self.assertIsNone(created['error'])
        self.assertEqual(created['data']['blockers'], [])
        self.preview = self.machine.persisted_preview_for_ask(
            REPO, ACTOR, created['data']['previewId'], GEN)
        self.ingress = WriteIngressProfile(
            read_bearer=READ, write_bearer=WRITE,
            allowed_repos=[REPO], signing_pins=self.pins)
        self.bridge = PrivateWriteBridge(
            ingress=self.ingress, reads=self.reads,
            gateway_generation=GEN, source_enabled=True)
        self.bridge._machine.clock = self.machine.clock
        self.dispatch = self.signed_dispatch()

    def signed_dispatch(self, *, request_id=EFFECT, actor=ACTOR,
                        target_sha=None, signer=None, key_id='fixture'):
        snapshot = self.preview['snapshot']
        fields = dict(requestId=request_id, repoId=REPO, actor=actor,
                      previewId=snapshot['previewId'],
                      expectedHead=snapshot['expectedHead'],
                      targetSha=snapshot['targetSha'] if target_sha is None else target_sha,
                      configGeneration=snapshot['configGeneration'])
        identity = dict(issuer='fortis-gateway', audience='fortis-mirror',
                        service='mirror', operation='execute_windows_pull_v2',
                        **fields)
        claims = {**identity,
                  'fingerprint': hashlib.sha256(_canonical(identity)).hexdigest(),
                  'issuedAt': TIME, 'expiresAt': TIME + 100}
        signature = (self.signer if signer is None else signer).sign(
            DOMAIN + _canonical(claims))
        return {**fields, 'proof': {
            'schemaVersion': 1, 'keyId': key_id, 'claims': claims,
            'signature': base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}}

    def send(self, dispatch=None, *, token=WRITE, bridge=None):
        document = self.dispatch if dispatch is None else dispatch
        raw = json.dumps(document, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True).encode('ascii')
        headers = [('Host', 'host.docker.internal:443'),
                   ('Authorization', 'Bearer ' + token),
                   ('Content-Type', 'application/json'),
                   ('Content-Length', str(len(raw)))]
        return (self.bridge if bridge is None else bridge).process_envelope(
            method='POST', path=PATH, headers=headers, body=raw)

    def counts(self):
        with self.machine.worker._db() as db:
            return (
                db.execute('SELECT COUNT(*) FROM machine_bindings').fetchone()[0],
                db.execute('SELECT COUNT(*) FROM pull_receipts').fetchone()[0])

    def test_real_valid_ed25519_one_effect_then_lookup_after_proof_expiry(self):
        with patch.object(self.machine.worker, '_execute',
                          return_value={'requestId': EFFECT, 'state': 'EXECUTED',
                                        'head': self.dispatch['targetSha']}) as git:
            accepted = self.send()
            self.assertEqual((accepted['workerState'], accepted['effect']),
                             ('EXECUTED', 'CONFIRMED'))
            self.assertEqual(self.counts(), (1, 1))
            self.ticks[0] += 300
            # An old UUID cannot be turned into a new effect, even when the
            # proof is now expired or a tampered signature is provided.
            replay = copy.deepcopy(self.dispatch)
            replay['proof']['signature'] = 'A' * 86
            receipt = self.send(replay)
            self.assertEqual((receipt['workerState'], receipt['effect']),
                             ('EXECUTED', 'CONFIRMED'))
            self.assertEqual(git.call_count, 1)
        self.assertEqual(self.counts(), (1, 1))

    def test_real_invalid_signature_and_wrong_signer_leave_ledger_empty(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        for name, changed in (
            ('bad-signature-byte', self._tamper_signature(self.dispatch)),
            ('different-private-signer', self.signed_dispatch(
                signer=Ed25519PrivateKey.generate())),
        ):
            with self.subTest(name=name), \
                 patch.object(self.machine.worker, '_execute') as git, \
                 self.assertRaisesRegex(ReadError, 'SIGNATURE_INVALID'):
                self.send(changed)
            git.assert_not_called()
            self.assertEqual(self.counts(), (0, 0))

    @staticmethod
    def _tamper_signature(dispatch):
        changed = copy.deepcopy(dispatch)
        sig = bytearray(base64.urlsafe_b64decode(changed['proof']['signature'] + '=='))
        sig[0] ^= 1
        changed['proof']['signature'] = (
            base64.urlsafe_b64encode(sig).decode('ascii').rstrip('='))
        return changed

    def test_real_signed_foreign_actor_or_different_target_is_not_permission(self):
        for label, changed in (
            ('other-actor-with-valid-signature', self.signed_dispatch(actor='repo:foreign')),
            ('other-target-with-valid-signature', self.signed_dispatch(target_sha='f' * 40)),
        ):
            with self.subTest(label=label), \
                 patch.object(self.machine.worker, '_execute') as git, \
                 self.assertRaises(ReadError):
                self.send(changed)
            git.assert_not_called()
            self.assertEqual(self.counts(), (0, 0))

    def test_real_signed_old_preview_cannot_survive_owner_pin_rotation(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        successor = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization
        new_public = successor.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        rotated = PinnedEd25519Keys({'fixture': new_public}, owner_epoch='f' * 64)
        new_reads = CoreReadV2(self.core, allowed_repos=[REPO],
                               inventories={'mirror-pilot': [REPO]},
                               machine_generation='c' * 64,
                               owner_signing_pins=rotated)
        new_bridge = PrivateWriteBridge(
            ingress=WriteIngressProfile(read_bearer=READ, write_bearer=WRITE,
                                        allowed_repos=[REPO], signing_pins=rotated),
            reads=new_reads, gateway_generation=GEN, source_enabled=True)
        new_bridge._machine.clock = self.machine.clock
        with patch.object(self.machine.worker, '_execute') as git, \
             self.assertRaisesRegex(ReadError, 'PREVIEW_STALE'):
            self.send(bridge=new_bridge)
        git.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))

    def test_real_signature_is_not_enough_without_write_bearer_or_fresh_ttl(self):
        with patch.object(self.machine.worker, '_execute') as git, \
             self.assertRaisesRegex(ReadError, 'PERMISSION_DENIED'):
            self.send(token=READ)
        git.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))
        self.ticks[0] = TIME + 121
        with patch.object(self.machine.worker, '_execute') as git, \
             self.assertRaises(ReadError):
            self.send()
        git.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))


@unittest.skipUnless(importlib.util.find_spec('cryptography'),
                     'P4 real TLS fixtures require cryptography in hosted Windows CI')
class HostedWindowsP4MutualTLSTests(unittest.TestCase):
    """Real ephemeral CA, verified mTLS and exact client DER pin, no live host.

    Positive paths use the existing UNREGISTERED handler on 127.0.0.1 only;
    the owner-installed HTTPS server factory remains GET-only. Git is mocked.
    """
    signed_dispatch = HostedWindowsP4CryptoTests.signed_dispatch

    def setUp(self):
        import datetime
        import ipaddress
        import ssl
        import threading
        from http.server import ThreadingHTTPServer

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        from mirror_api.write_v2_listener import PrivateWriteV2Handler

        # Reuse the real Ed25519 signer and in-memory actor-bound preview, not
        # its mock-signature test fixture or any owner/Windows credentials.
        HostedWindowsP4CryptoTests.setUp(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        utc = datetime.timezone.utc
        now = datetime.datetime.now(utc)

        def key():
            return rsa.generate_private_key(public_exponent=65537, key_size=2048)

        ca_key = key()
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                                  'P4 ephemeral test CA')])
        ca = (x509.CertificateBuilder().subject_name(ca_name)
              .issuer_name(ca_name).public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number())
              .not_valid_before(now - datetime.timedelta(minutes=2))
              .not_valid_after(now + datetime.timedelta(hours=1))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0),
                             critical=True)
              .sign(ca_key, hashes.SHA256()))
        ca_pem = ca.public_bytes(serialization.Encoding.PEM)
        ca_file = root / 'ca.pem'
        ca_file.write_bytes(ca_pem)

        def issue(name, *, server):
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
            private = key()
            builder = (x509.CertificateBuilder().subject_name(subject)
                       .issuer_name(ca.subject).public_key(private.public_key())
                       .serial_number(x509.random_serial_number())
                       .not_valid_before(now - datetime.timedelta(minutes=2))
                       .not_valid_after(now + datetime.timedelta(minutes=40))
                       .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                                      critical=True)
                       .add_extension(x509.ExtendedKeyUsage([
                           ExtendedKeyUsageOID.SERVER_AUTH if server
                           else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False))
            if server:
                builder = builder.add_extension(x509.SubjectAlternativeName([
                    x509.DNSName('host.docker.internal'),
                    x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),
                    critical=False)
            cert = builder.sign(ca_key, hashes.SHA256())
            crt_path = root / (name + '.crt')
            key_path = root / (name + '.key')
            crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
            return crt_path, key_path, cert.public_bytes(serialization.Encoding.DER)

        server_crt, server_key, _ = issue('test-server', server=True)
        client_crt, client_key, client_der = issue('expected-gateway', server=False)
        other_crt, other_key, _ = issue('other-gateway', server=False)

        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        server_context.load_cert_chain(str(server_crt), str(server_key))
        server_context.load_verify_locations(cafile=str(ca_file))
        server_context.verify_mode = ssl.CERT_REQUIRED

        self.server = ThreadingHTTPServer(('127.0.0.1', 0),
                                          PrivateWriteV2Handler)
        self.server.daemon_threads = True
        self.server.socket = server_context.wrap_socket(
            self.server.socket, server_side=True)
        self.server.owner_write_opt_in = True
        self.server.private_write_bridge = self.bridge
        self.server.gateway_secret = READ
        self.server.allowed_repos = self.reads.allowed_repos
        self.server.read_v2 = self.reads
        self.server.mirror = self.core
        expected = hashlib.sha256(client_der).digest()
        self.observed_tls = []

        def exact_owner_peer_gate(cert_der, peer):
            # Stronger than just host address: enforce the *verified client*
            # DER fingerprint. Cert and peer are passed by the TLS handler,
            # not from untrusted HTTP headers.
            self.observed_tls.append((cert_der, peer))
            return (type(peer) is tuple and len(peer) == 2 and
                    peer[0] == '127.0.0.1' and
                    hashlib.sha256(cert_der).digest() == expected)

        self.server.owner_write_peer_gate = exact_owner_peer_gate
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        # LIFO cleanup: shutdown thread -> join -> close socket.
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.shutdown)

        def client_context(crt, private):
            context = ssl.create_default_context(
                ssl.Purpose.SERVER_AUTH, cafile=str(ca_file))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            if crt is not None:
                context.load_cert_chain(str(crt), str(private))
            return context

        self.good_context = client_context(client_crt, client_key)
        self.other_context = client_context(other_crt, other_key)
        self.no_client_context = client_context(None, None)
        self.untrusted_server_context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH)  # system roots do not contain test CA

    def https(self, *, context=None, bearer=WRITE, body=None):
        import http.client
        context = self.good_context if context is None else context
        raw = (json.dumps(self.dispatch, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True).encode('ascii')
               if body is None else body)
        conn = http.client.HTTPSConnection('127.0.0.1', self.server.server_port,
                                            timeout=6, context=context)
        try:
            conn.putrequest('POST', '/gateway/v2/repositories/' + REPO + '/pull',
                            skip_host=True, skip_accept_encoding=True)
            conn.putheader('Host', 'host.docker.internal:443')
            conn.putheader('Authorization', 'Bearer ' + bearer)
            conn.putheader('Content-Type', 'application/json')
            conn.putheader('Content-Length', str(len(raw)))
            conn.endheaders(raw)
            response = conn.getresponse()
            return response.status, json.loads(response.read().decode('ascii'))
        finally:
            conn.close()

    def test_real_tls_mutual_auth_pinned_client_cert_and_real_ed25519(self):
        with patch.object(self.machine.worker, '_execute',
                          return_value={'requestId': EFFECT, 'state': 'EXECUTED',
                                        'head': self.dispatch['targetSha']}) as git:
            status, result = self.https()
            self.assertEqual(status, 200)
            self.assertEqual((result['workerState'], result['effect']),
                             ('EXECUTED', 'CONFIRMED'))
            replay_status, replay = self.https()
            self.assertEqual((replay_status, replay['effect']), (200, 'CONFIRMED'))
            self.assertEqual(git.call_count, 1)
        self.assertEqual(len(self.observed_tls), 2)
        self.assertTrue(all(type(item[0]) is bytes and item[0] for item in
                            self.observed_tls))
        self.assertEqual(HostedWindowsP4CryptoTests.counts(self), (1, 1))

    def test_real_mtls_unpinned_client_and_read_bearer_never_run_git(self):
        with patch.object(self.machine.worker, '_execute') as git:
            status, denied = self.https(context=self.other_context)
            self.assertEqual((status, denied['error']),
                             (403, 'GATEWAY_WRITE_TRANSPORT_DENIED'))
            status, denied = self.https(bearer=READ)
            self.assertEqual((status, denied['error']),
                             (403, 'GATEWAY_WRITE_DENIED'))
            git.assert_not_called()
        self.assertEqual(HostedWindowsP4CryptoTests.counts(self), (0, 0))
        self.assertEqual(len(self.observed_tls), 2)

    def test_real_tls_requires_client_cert_trusted_server_and_owner_optin(self):
        import ssl
        with patch.object(self.machine.worker, '_execute') as git:
            with self.assertRaises((ssl.SSLError, ConnectionError, OSError)):
                self.https(context=self.no_client_context)
            with self.assertRaises((ssl.SSLError, ConnectionError, OSError)):
                self.https(context=self.untrusted_server_context)
            self.server.owner_write_opt_in = False
            status, denied = self.https()
            self.assertEqual((status, denied['error']), (405, 'GATEWAY_READ_ONLY'))
            git.assert_not_called()
        self.assertEqual(HostedWindowsP4CryptoTests.counts(self), (0, 0))
