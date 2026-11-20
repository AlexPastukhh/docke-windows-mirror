"""P5 S3 isolated explicit launcher guards; never binds port 443 or calls Gateway."""
import argparse
import contextlib
import io
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from mirror_api import gateway_v2_launcher as launcher
from mirror_api.server import MirrorServer


class LaunchFixtures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mirror-v2-launch-test-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.windows = root / 'Repos'
        self.windows.mkdir()
        self.appdata = root / 'LocalAppData'
        self.ledger_dir = self.appdata / 'FortisMirror'
        self.ledger_dir.mkdir(parents=True)
        self.ledger = self.ledger_dir / 'pull-receipts.sqlite3'
        self.ledger.write_bytes(launcher.SQLITE_HEADER + b'\x00' * 128)
        self.private = root / 'owner-private'
        self.private.mkdir()
        self.cert, self.key, self.secret = [self.private / name for name in
                                            ('server.pem', 'server-key.pem', 'read-bearer.txt')]
        for item in (self.cert, self.key, self.secret):
            item.write_text('synthetic-fixture-only', encoding='ascii')
        self.flags = ['--windows-root', str(self.windows), '--port', '8765',
                      '--cert', str(self.cert), '--key', str(self.key),
                      '--secret-file', str(self.secret),
                      '--machine-generation', 'a'*64]
        self.args = argparse.Namespace(windows_root=str(self.windows), port=8765,
            cert=str(self.cert), key=str(self.key), secret_file=str(self.secret),
            machine_generation='a'*64)
        patch_platform = mock.patch.object(launcher.sys, 'platform', 'win32')
        patch_env = mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.appdata)})
        patch_platform.start(); patch_env.start()
        self.addCleanup(patch_env.stop); self.addCleanup(patch_platform.stop)

    def test_exact_default_receipt_file_only_header_no_sqlite_effect(self):
        original = self.ledger.read_bytes()
        self.assertEqual(launcher.existing_default_ledger(), self.ledger)
        launcher.validate(self.args)
        self.assertEqual(original, self.ledger.read_bytes())
        self.assertFalse(self.ledger.with_name(self.ledger.name + '-wal').exists())
        self.assertFalse(self.ledger.with_name(self.ledger.name + '-shm').exists())

    def test_invalid_generation_browser_443_and_root_fail_before_bind(self):
        for field, value, code in (
                ('machine_generation', 'A'*64, 'MIRROR_V2_OWNER_GENERATION_REQUIRED'),
                ('machine_generation', 'not-a-sha', 'MIRROR_V2_OWNER_GENERATION_REQUIRED'),
                ('port', 443, 'MIRROR_V2_BROWSER_PORT_DENIED'),
                ('port', 2, 'MIRROR_V2_BROWSER_PORT_DENIED'),
                ('windows_root', 'relative', 'MIRROR_V2_WINDOWS_ROOT_REQUIRED')):
            with self.subTest(field=field, value=value):
                opts = argparse.Namespace(**vars(self.args))
                setattr(opts, field, value)
                with self.assertRaisesRegex(launcher.LaunchStop, code):
                    launcher.validate(opts)
        with mock.patch.object(launcher.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(launcher.LaunchStop, 'MIRROR_V2_WINDOWS_REQUIRED'):
                launcher.validate(self.args)

    def test_invalid_or_missing_ledger_stops_without_creating_another(self):
        self.ledger.unlink()
        with self.assertRaisesRegex(launcher.LaunchStop, 'MIRROR_V2_DEFAULT_LEDGER_REQUIRED'):
            launcher.validate(self.args)
        self.assertFalse(self.ledger.exists())
        self.ledger.write_bytes(b'invalid' + b'0'*120)
        with self.assertRaisesRegex(launcher.LaunchStop, 'MIRROR_V2_LEDGER_HEADER_INVALID'):
            launcher.validate(self.args)
        self.ledger.write_bytes(launcher.SQLITE_HEADER + b'0'*128)
        self.ledger_dir.rename(self.appdata / 'original')
        with self.assertRaisesRegex(launcher.LaunchStop, 'MIRROR_V2_DEFAULT_LEDGER_REQUIRED'):
            launcher.validate(self.args)
        self.assertFalse(self.ledger_dir.exists())

    def test_credential_paths_are_private_files_not_source_or_symlinks(self):
        opts = argparse.Namespace(**vars(self.args))
        opts.secret_file = str(self.windows / 'missing.txt')
        with self.assertRaisesRegex(launcher.LaunchStop, 'MIRROR_V2_PRIVATE_FILES_REQUIRED'):
            launcher.validate(opts)
        link = self.private / 'alias.txt'
        try:
            link.symlink_to(self.secret)
        except (OSError, NotImplementedError):
            self.skipTest('Host cannot create test symlink')
        opts.secret_file = str(link)
        with self.assertRaisesRegex(launcher.LaunchStop, 'MIRROR_V2_PRIVATE_FILES_REQUIRED'):
            launcher.validate(opts)

    def test_real_browser_core_is_single_lazy_default_writer(self):
        backend = SimpleNamespace(windows_root=self.windows)
        browser = MirrorServer(('127.0.0.1', 0), backend)
        try:
            self.assertIs(browser.browser_pull.core, browser.core)
            self.assertIsNone(browser.core.receipt_db)
            self.assertFalse(browser.core.read_only)
            self.assertIsNone(browser.core._worker)
            self.assertIsNone(browser.core._guard)
        finally:
            browser.server_close()

    def test_main_passes_same_core_and_closed_pilot_to_v1_v2_adapter(self):
        events = []
        instances = []
        class FakeBrowser:
            def __init__(self, address, mirror, *, allow_browser_pull=True, receipt_db=None):
                self.core = object()
                self.server_port = address[1]
                self.bootstrap_token = 'test-fixture-only'
                self.closed = False
                events.append('browser_started')
                instances.append(self)
                self.test_allow_pull = allow_browser_pull
                self.test_db = receipt_db
            def serve_forever(self):
                events.append('browser_served')
            def server_close(self):
                self.closed = True
                events.append('browser_closed')
        class FakeReadServer:
            def __init__(self):
                self.stop = threading.Event()
                self.closed = False
            def serve_forever(self):
                self.stop.wait(3)
            def shutdown(self):
                self.stop.set()
            def server_close(self):
                self.closed = True
                events.append('tls_closed')
        external = FakeReadServer()
        def factory(core, **kwargs):
            self.assertIs(core, instances[0].core)
            self.assertEqual(kwargs['bind'], '127.0.0.1')
            self.assertEqual(kwargs['allowed_repos'], ('fortis-approvals',))
            self.assertEqual(kwargs['inventories'],
                             {'mirror-pilot': ('fortis-approvals',)})
            self.assertEqual(kwargs['machine_generation'], 'a'*64)
            events.append('tls_factory')
            return external
        with mock.patch.object(launcher, 'MirrorServer', FakeBrowser), \
             mock.patch.object(launcher, 'create_server', side_effect=factory), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = launcher.main(self.flags)
        self.assertEqual(code, 0)
        self.assertTrue(external.closed)
        self.assertTrue(instances[0].closed)
        self.assertTrue(instances[0].test_allow_pull)
        self.assertIsNone(instances[0].test_db)
        self.assertEqual(events[0:2], ['browser_started', 'tls_factory'])
        self.assertEqual(events[-2:], ['tls_closed', 'browser_closed'])
        self.assertIn('OWNER_ACCEPTANCE_REQUIRED', stdout.getvalue())

    def test_port443_occupied_fails_closed_and_closes_browser(self):
        browsers = []
        class FakeBrowser:
            def __init__(self, address, backend, **kwargs):
                self.core = object()
                self.closed = False
                browsers.append(self)
            def server_close(self):
                self.closed = True
        with mock.patch.object(launcher, 'MirrorServer', FakeBrowser), \
             mock.patch.object(launcher, 'create_server',
                               side_effect=OSError('fixture occupied port 443')) as listen, \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = launcher.main(self.flags)
        self.assertEqual(result, 2)
        self.assertTrue(browsers[0].closed)
        self.assertEqual(listen.call_count, 1)
        self.assertEqual(stderr.getvalue().strip(), 'MIRROR_V2_LAUNCH_FAILED_NO_CUTOVER')

    def test_no_custom_receipt_db_cli_and_default_ps_entrypoint_preserved(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                launcher.main(self.flags + ['--receipt-db', str(self.ledger)])
        self.assertEqual(result.exception.code, 2)
        ps = (Path(__file__).parent / 'Start-MirrorAPI.ps1').read_text()
        self.assertIn("[switch]$GatewayReadV2", ps)
        self.assertIn("'mirror_api.gateway_v2_launcher'", ps)
        self.assertIn("'mirror_api.server'", ps)
        self.assertIn("'MIRROR_V2_EXPLICIT_OPT_IN_REQUIRED'", ps)
        self.assertNotIn("--gateway-receipt-db", ps)


if __name__ == '__main__':
    unittest.main()
