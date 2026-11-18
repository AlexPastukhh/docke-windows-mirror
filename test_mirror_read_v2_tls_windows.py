"""Hosted-Windows-capable real TLS fixture for the DISABLED Mirror READ v2 factory.

The fixture uses a synthetic ephemeral CA, synthetic leaf private key and
random localhost port. It never connects to the owner Windows listener, calls
GitHub, opens port 443, provisions Gateway credentials or enables WRITE.
TLS handshake and hostname trust are real; only the port binder is intercepted.
"""
from datetime import datetime, timedelta, timezone
from http.client import HTTPResponse
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch

from mirror_api.core import MirrorCore
from mirror_api.gateway_v2 import create_server
from test_mirror_read_v2 import Backend


@unittest.skipUnless(importlib.util.find_spec('cryptography'),
                     'Pinned cryptography runs in hosted Windows Core CI')
class ReadV2RealTLSWindowsTests(unittest.TestCase):
    def setUp(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        temp = tempfile.TemporaryDirectory(prefix='mirror-v2-tls-fixture-')
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.ca, self.cert, self.key, secret = (
            base / name for name in ('ca.pem', 'leaf.pem', 'leaf-key.pem',
                                    'read-token.txt'))
        self.token = 'r' * 48
        secret.write_text(self.token, encoding='ascii')

        def rsa_key():
            return rsa.generate_private_key(public_exponent=65537, key_size=2048)

        now = datetime.now(timezone.utc)
        ca_key = rsa_key()
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                               'Mirror fixture-only CA')])
        ca_cert = (
            x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
            .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(ca_key, hashes.SHA256()))
        self.ca.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

        leaf_key = rsa_key()
        leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                                 'host.docker.internal')])
        leaf_cert = (
            x509.CertificateBuilder().subject_name(leaf_name)
            .issuer_name(ca_cert.subject).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(hours=12))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                           critical=True)
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName('host.docker.internal')]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
        self.cert.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
        self.key.write_bytes(leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))

        self.backend = Backend()
        self.core = MirrorCore(self.backend, read_only=True)
        self.addCleanup(self.core.close)
        # Production source insists on port 443. Intercept ONLY the socket
        # binding for this isolated test so it cannot displace the live owner.
        def bind_ephemeral(address, handler):
            self.assertEqual(address[1], 443)
            self.assertEqual(address[0], '127.0.0.1')
            return ThreadingHTTPServer((address[0], 0), handler)

        with patch('mirror_api.gateway_readonly.ThreadingHTTPServer',
                   side_effect=bind_ephemeral):
            self.server = create_server(
                self.core, bind='127.0.0.1', cert=self.cert, key=self.key,
                secret_file=secret, allowed_repos=['fortis-approvals'],
                inventories={'mirror-pilot': ['fortis-approvals']})
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.shutdown)

    def context(self, *, ca=None):
        ctx = ssl.create_default_context(
            cafile=str(self.ca if ca is None else ca))
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        return ctx

    def connect(self, *, context=None, sni='host.docker.internal'):
        raw = socket.create_connection(('127.0.0.1', self.server.server_port),
                                       timeout=3)
        try:
            tls = (self.context() if context is None else context).wrap_socket(
                raw, server_hostname=sni)
            tls.settimeout(3)
            return tls
        except Exception:
            raw.close()
            raise

    def request(self, method, path, *, token='valid',
                host='host.docker.internal', cookie=False):
        headers = [f'Host: {host}', 'Connection: close']
        if token is not None:
            headers.append('Authorization: Bearer ' +
                           (self.token if token == 'valid' else 'wrong'))
        if cookie:
            headers.append('Cookie: owner_session=untrusted')
        data = (f'{method} {path} HTTP/1.1\r\n' +
                '\r\n'.join(headers) + '\r\n\r\n').encode('ascii')
        with self.connect() as tls:
            self.assertEqual(tls.version() in ('TLSv1.2', 'TLSv1.3'), True)
            tls.sendall(data)
            response = HTTPResponse(tls)
            response.begin()
            return response.status, json.loads(response.read().decode('utf-8'))

    def test_real_tls_san_ca_and_v1_v2_get_same_core(self):
        with self.connect() as tls:
            self.assertIsNotNone(tls.getpeercert())
            self.assertEqual(tls.getpeercert()['subjectAltName'],
                             (('DNS', 'host.docker.internal'),))
        v1_status, v1 = self.request('GET',
            '/gateway/v1/repositories/fortis-approvals')
        v2_status, v2 = self.request('GET',
            '/gateway/v2/repositories/fortis-approvals')
        self.assertEqual((v1_status, v1['schemaVersion']), (200, 1))
        self.assertEqual((v2_status, v2['schemaVersion']), (200, 2))
        self.assertEqual(v1['repository']['repoId'], v2['target'])
        self.assertIsNone(v2['error'])
        self.assertFalse(hasattr(self.server, 'worker'))
        self.assertIsNone(self.core._worker)
        self.assertIsNone(self.core._guard)

    def test_real_tls_rejects_missing_bearer_wrong_host_and_all_writes(self):
        before = len(self.backend.calls)
        for token, host, cookie, status in (
            (None, 'host.docker.internal', False, 401),
            ('wrong', 'host.docker.internal', False, 401),
            ('valid', 'evil.invalid', False, 403),
            ('valid', 'host.docker.internal', True, 403)):
            with self.subTest(token=token, host=host, cookie=cookie):
                code, _ = self.request('GET',
                    '/gateway/v2/repositories/fortis-approvals',
                    token=token, host=host, cookie=cookie)
                self.assertEqual(code, status)
        self.assertEqual(len(self.backend.calls), before)
        for verb in ('POST', 'PUT', 'PATCH', 'DELETE'):
            with self.subTest(verb=verb):
                code, result = self.request(
                    verb, '/gateway/v2/repositories/fortis-approvals')
                self.assertEqual(code, 405)
                self.assertEqual(result['error'], 'GATEWAY_READ_ONLY')
        self.assertEqual(len(self.backend.calls), before)
        self.assertIsNone(self.core._worker)

    def test_real_tls_wrong_ca_and_wrong_hostname_fail_during_handshake(self):
        # No insecure SSLContext, verify=False, proxy or HTTP fallback.
        with self.assertRaises(ssl.SSLCertVerificationError):
            with self.connect(sni='not-host.docker.internal'):
                pass
        with self.assertRaises(ssl.SSLCertVerificationError):
            with self.connect(context=ssl.create_default_context()):
                pass
        self.assertEqual(self.backend.calls, [])
        self.assertIsNone(self.core._worker)


if __name__ == '__main__':
    unittest.main()
