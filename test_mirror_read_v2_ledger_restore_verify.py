"""P5 offline synthetic ledger restore checks; never touches a real Windows ledger."""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from tools.mirror_read_v2_ledger_restore_verify import EvidenceStop, main, verify

BACKUP = 'consistent-20261119.sqlite3'


class IsolatedLedgerRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mirror-p5-synthetic-')
        self.addCleanup(self.tmp.cleanup)
        self.local = Path(self.tmp.name) / 'OwnerLocal'
        root = self.local / 'FortisMirrorP5Evidence'
        self.backups = root / 'backups'
        self.restores = root / 'restores'
        self.backups.mkdir(parents=True)
        self.restores.mkdir()
        self.backup = self.backups / BACKUP
        self.live = self.local / 'FortisMirror' / 'pull-receipts.sqlite3'
        self.live.parent.mkdir()
        self.live.write_bytes(b'LIVE_LEDGER_SENTINEL_UNOPENED')
        self.payload = 'private-payload-sentinel-do-not-leak'
        self.store_id = str(uuid.uuid4())
        self.create_backup()

    def create_backup(self, *, with_machine=False):
        db = sqlite3.connect(str(self.backup))
        with db:
            db.execute('CREATE TABLE pull_receipts (request_id TEXT PRIMARY KEY,'
                       ' payload TEXT NOT NULL, state TEXT NOT NULL, result TEXT)')
            db.execute('CREATE TABLE mirror_store_meta (name TEXT PRIMARY KEY,'
                       'value TEXT NOT NULL)')
            db.execute('INSERT INTO mirror_store_meta VALUES(?,?)',
                       ('store_id', self.store_id))
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (str(uuid.uuid4()), self.payload, 'UNCERTAIN',
                        '{"sensitive":"fake"}'))
            if with_machine:
                db.execute('CREATE TABLE machine_previews ('
                           'request_id TEXT PRIMARY KEY, actor TEXT NOT NULL,'
                           'repo_id TEXT NOT NULL, gateway_generation TEXT NOT NULL,'
                           'config_generation TEXT NOT NULL, preview_id TEXT UNIQUE,'
                           'expires_at INTEGER NOT NULL, origin TEXT, data TEXT NOT NULL)')
                db.execute('CREATE TABLE machine_bindings ('
                           'request_id TEXT PRIMARY KEY, actor TEXT NOT NULL,'
                           'repo_id TEXT NOT NULL, identity TEXT NOT NULL)')
                db.execute('INSERT INTO machine_previews VALUES(?,?,?,?,?,?,?,?,?)',
                           (str(uuid.uuid4()), 'fixture', 'fortis-approvals',
                            'f'*64, 'a'*64, str(uuid.uuid4()), 1234, 'fixture', '{}'))
        db.close()

    def run_verify(self, *, backup_name=BACKUP, restore_name='test-new'):
        return verify(localappdata=self.local, backup_name=backup_name,
                      restore_name=restore_name)

    def test_isolated_restore_preserves_existing_receipt_not_production(self):
        before = self.backup.read_bytes()
        result = self.run_verify()
        self.assertEqual(result['state'], 'MANUAL_REQUIRED')
        self.assertEqual(result['isolatedBackupRestore'], 'PASS')
        self.assertIs(result['cutoverAuthorized'], False)
        self.assertEqual(result['rowCounts'],
                         {'mirror_store_meta': 1, 'pull_receipts': 1})
        self.assertEqual(self.backup.read_bytes(), before)
        self.assertEqual(self.live.read_bytes(), b'LIVE_LEDGER_SENTINEL_UNOPENED')
        self.assertEqual(list(self.backups.iterdir()), [self.backup])
        restored = self.restores / 'test-new' / 'restored.sqlite3'
        self.assertTrue(restored.is_file())
        self.assertNotIn(self.payload, json.dumps(result))
        self.assertNotIn(self.store_id, json.dumps(result))
        self.assertNotIn(str(self.local), json.dumps(result))
        with sqlite3.connect(str(restored)) as db:
            self.assertEqual(db.execute(
                'SELECT state FROM pull_receipts').fetchone()[0], 'UNCERTAIN')

    def test_machine_tables_are_preserved_when_present(self):
        self.backup.unlink()
        self.create_backup(with_machine=True)
        result = self.run_verify()
        self.assertEqual(result['rowCounts']['machine_previews'], 1)
        self.assertEqual(result['rowCounts']['machine_bindings'], 0)
        self.assertFalse(result['cutoverAuthorized'])

    def test_malformed_backup_and_unknown_schema_stop_before_restore(self):
        self.backup.write_bytes(b'not sqlite' * 100)
        with self.assertRaises(sqlite3.DatabaseError):
            self.run_verify()
        self.assertFalse((self.restores / 'test-new').exists())
        self.backup.unlink()
        self.create_backup()
        with sqlite3.connect(str(self.backup)) as db:
            db.execute('CREATE TABLE unreviewed_private_data (secret TEXT)')
        with self.assertRaisesRegex(EvidenceStop, 'LEDGER_TABLE_SET_UNREVIEWED'):
            self.run_verify()
        self.assertFalse((self.restores / 'test-new').exists())

    def test_missing_store_id_stops_without_restore(self):
        with sqlite3.connect(str(self.backup)) as db:
            db.execute('DELETE FROM mirror_store_meta')
        with self.assertRaisesRegex(EvidenceStop, 'STORE_ID_MISSING'):
            self.run_verify()
        self.assertFalse((self.restores / 'test-new').exists())

    def test_refuses_sidecars_existing_target_and_bad_names(self):
        sidecar = Path(str(self.backup) + '-wal')
        sidecar.write_bytes(b'dummy')
        with self.assertRaisesRegex(EvidenceStop, 'BACKUP_NOT_SELF_CONTAINED'):
            self.run_verify()
        sidecar.unlink()
        (self.restores / 'test-new').mkdir()
        with self.assertRaisesRegex(EvidenceStop, 'ISOLATED_DESTINATION_EXISTS'):
            self.run_verify()
        for value in ('../pull-receipts.sqlite3', 'X.sqlite3', 'live.db', ''):
            with self.subTest(backup=value):
                with self.assertRaisesRegex(EvidenceStop, 'BACKUP_NAME_INVALID'):
                    self.run_verify(backup_name=value)
        with self.assertRaisesRegex(EvidenceStop, 'RESTORE_NAME_INVALID'):
            self.run_verify(restore_name='../live')

    def test_missing_evidence_roots_are_not_created(self):
        self.backup.unlink()
        with self.assertRaisesRegex(EvidenceStop, 'BACKUP_FILE_INVALID'):
            self.run_verify()
        self.backups.rmdir()
        with self.assertRaisesRegex(EvidenceStop, 'OWNER_PRIVATE_EVIDENCE_ROOT_REQUIRED'):
            self.run_verify()

    def test_hard_link_and_symlink_backup_are_rejected(self):
        duplicate = self.backups / 'duplicate.sqlite3'
        duplicate.hardlink_to(self.backup)
        with self.assertRaisesRegex(EvidenceStop, 'BACKUP_FILE_INVALID'):
            self.run_verify()
        duplicate.unlink()
        link = self.backups / 'alias.sqlite3'
        try:
            link.symlink_to(self.backup)
        except OSError:
            self.skipTest('symlink unavailable without Windows Developer Mode')
        with self.assertRaisesRegex(EvidenceStop, 'BACKUP_FILE_INVALID'):
            self.run_verify(backup_name='alias.sqlite3')

    def test_windows_entrypoint_success_remains_manual_required(self):
        out = io.StringIO()
        with patch('sys.platform', 'win32'), patch.dict('os.environ',
                {'LOCALAPPDATA': str(self.local)}), patch('sys.stdout', out):
            code = main(['--backup-name', BACKUP, '--restore-name', 'cli-new'])
        self.assertEqual(code, 3)
        report = json.loads(out.getvalue())
        self.assertEqual(report['state'], 'MANUAL_REQUIRED')
        self.assertFalse(report['cutoverAuthorized'])
        self.assertNotIn(self.payload, out.getvalue())
        self.assertTrue((self.restores / 'cli-new' / 'restored.sqlite3').is_file())

    def test_offline_linux_entrypoint_will_not_open_any_ledger(self):
        out = io.StringIO()
        with patch('sys.platform', 'linux'), patch('sys.stdout', out):
            code = main(['--backup-name', BACKUP, '--restore-name', 'new'])
        self.assertEqual(code, 2)
        result = json.loads(out.getvalue())
        self.assertEqual(result['code'], 'WINDOWS_OWNER_REQUIRED')
        self.assertFalse(result['cutoverAuthorized'])
        self.assertFalse((self.restores / 'new').exists())


if __name__ == '__main__':
    unittest.main()
