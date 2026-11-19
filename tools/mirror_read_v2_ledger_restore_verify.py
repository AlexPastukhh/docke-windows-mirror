"""P5 offline owner-only verification of an ALREADY CONSISTENT SQLite backup.

Never opens the live Mirror ledger. Requires an owner-private evidence root
outside the source clone: %LOCALAPPDATA%/FortisMirrorP5Evidence/{backups,restores}.
All readback is from the supplied backup and a NEW isolated SQLite restore.
No listener, Git, Gateway, credentials, Windows Pull or production mutations.
"""
import argparse
from contextlib import closing
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import uuid

BACKUP_NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,60}\.sqlite3\Z')
RESTORE_NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,62}\Z')
MAX_BACKUP_BYTES = 512 * 1024 * 1024
TABLES = {
    'pull_receipts': ('request_id', 'payload', 'state', 'result'),
    'mirror_store_meta': ('name', 'value'),
    'machine_previews': ('request_id', 'actor', 'repo_id', 'gateway_generation',
                         'config_generation', 'preview_id', 'expires_at', 'origin', 'data'),
    'machine_bindings': ('request_id', 'actor', 'repo_id', 'identity'),
}


class EvidenceStop(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise EvidenceStop(code)


def _plain(path):
    """No symlink/junction/reparse point anywhere in the selected path."""
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or (
                getattr(info, 'st_file_attributes', 0)
                & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            return False
    return True


def _read_only(path):
    # A backup is used, never a potentially live WAL database.
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA trusted_schema=OFF')
    return conn


def _validate(conn):
    _require(conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)],
             'SQLITE_INTEGRITY_FAILED')
    observed = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")
    }
    _require({'pull_receipts', 'mirror_store_meta'} <= observed and
             observed <= set(TABLES) and
             (('machine_previews' in observed) == ('machine_bindings' in observed)),
             'LEDGER_TABLE_SET_UNREVIEWED')
    for table in sorted(observed):
        actual = tuple(row[1] for row in conn.execute('PRAGMA table_info("' + table + '")'))
        _require(actual == TABLES[table], 'LEDGER_SCHEMA_UNREVIEWED')
    store_id = conn.execute(
        "SELECT value FROM mirror_store_meta WHERE name='store_id'").fetchall()
    _require(len(store_id) == 1 and type(store_id[0][0]) is str,
             'STORE_ID_MISSING')
    try:
        value = uuid.UUID(store_id[0][0])
        _require(value.version == 4 and str(value) == store_id[0][0],
                 'STORE_ID_INVALID')
    except (ValueError, AttributeError) as exc:
        raise EvidenceStop('STORE_ID_INVALID') from None
    counts = {}
    for table in sorted(observed):
        counts[table] = conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
    return counts


def _rows_match(source, restored, names):
    sentinel = object()
    for table in sorted(names):
        left = source.execute('SELECT * FROM "' + table + '" ORDER BY 1')
        right = restored.execute('SELECT * FROM "' + table + '" ORDER BY 1')
        for a, b in zip_longest(left, right, fillvalue=sentinel):
            if a != b:
                return False
    return True


def verify(*, localappdata, backup_name, restore_name):
    """Pure-path guarded isolated test. Call only on owner-captured backup."""
    _require(type(backup_name) is str and BACKUP_NAME.fullmatch(backup_name),
             'BACKUP_NAME_INVALID')
    _require(type(restore_name) is str and RESTORE_NAME.fullmatch(restore_name),
             'RESTORE_NAME_INVALID')
    base = Path(localappdata) if localappdata else None
    _require(base is not None and base.is_absolute() and base.is_dir() and
             _plain(base), 'OWNER_LOCALAPPDATA_INVALID')
    root = base / 'FortisMirrorP5Evidence'
    backups, restores = root / 'backups', root / 'restores'
    _require(all(p.is_dir() and _plain(p) for p in (root, backups, restores)),
             'OWNER_PRIVATE_EVIDENCE_ROOT_REQUIRED')
    source = backups / backup_name
    dest_dir = restores / restore_name
    dest = dest_dir / 'restored.sqlite3'
    _require(source.is_file() and _plain(source) and
             source.stat().st_nlink == 1 and
             100 <= source.stat().st_size <= MAX_BACKUP_BYTES,
             'BACKUP_FILE_INVALID')
    _require(not any(Path(str(source) + suffix).exists()
                     for suffix in ('-wal', '-shm', '-journal')),
             'BACKUP_NOT_SELF_CONTAINED')
    _require(not dest_dir.exists() and not dest_dir.is_symlink(),
             'ISOLATED_DESTINATION_EXISTS')
    # No source or destination can target the live FortisMirror directory.
    _require(source.parent == backups and dest_dir.parent == restores,
             'BACKUP_SCOPE_INVALID')

    with closing(_read_only(source)) as original:
        source_counts = _validate(original)
        # Owner-private root/ACL and quiescent backup provenance are manual gates.
        dest_dir.mkdir(mode=0o700)
        with closing(sqlite3.connect(str(dest), timeout=5)) as output:
            original.backup(output, pages=128, sleep=0.1)
        with closing(_read_only(dest)) as restored:
            target_counts = _validate(restored)
            _require(source_counts == target_counts and
                     _rows_match(original, restored, source_counts),
                     'RESTORE_DATA_MISMATCH')
    _require(not any(Path(str(dest) + suffix).exists()
                     for suffix in ('-wal', '-shm', '-journal')),
             'RESTORE_NOT_SELF_CONTAINED')
    # No receipt values, UUIDs, hashes, DB paths or full contents in stdout.
    return {
        'schemaVersion': 1, 'state': 'MANUAL_REQUIRED',
        'isolatedBackupRestore': 'PASS', 'cutoverAuthorized': False,
        'checks': ['BACKUP_INTEGRITY_PASS', 'RESTORE_INTEGRITY_PASS',
                   'SCHEMA_AND_ROW_COMPARISON_PASS'],
        'rowCounts': source_counts,
        'manualGates': [
            'INDEPENDENT_PROOF_BACKUP_FROM_LIVE_SHARED_CORE',
            'OWNER_PRIVATE_ACL_AND_WAL_CONSISTENCY',
            'RECEIPT_AND_UNCERTAIN_PRESERVATION',
            'OWNER_REVIEWED_ROLLBACK_AND_QUIESCENCE',
            'SEPARATE_PRODUCTION_GO',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-name', required=True)
    parser.add_argument('--restore-name', required=True)
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        report = {'state': 'STOP', 'code': 'WINDOWS_OWNER_REQUIRED',
                  'cutoverAuthorized': False}
    else:
        try:
            report = verify(localappdata=os.environ.get('LOCALAPPDATA'),
                            backup_name=args.backup_name,
                            restore_name=args.restore_name)
        except EvidenceStop as exc:
            report = {'state': 'STOP', 'code': str(exc),
                      'cutoverAuthorized': False}
        except Exception:
            # Do not expose private Windows paths or SQLite payload on failure.
            report = {'state': 'STOP', 'code': 'ISOLATED_SQLITE_TEST_FAILED',
                      'cutoverAuthorized': False}
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(',', ':')))
    return 3 if report['state'] == 'MANUAL_REQUIRED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
