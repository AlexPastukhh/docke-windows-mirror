r"""Owner-only READ v2 pre-cutover diagnostic. Never authorizes cutover.

Run from the owner Windows main clone after merge / owner-approved Pull:
  py -3 tools\mirror_read_v2_cutover_preflight.py --expected-source-sha <40-hex> \
      --machine-generation <owner-reviewed-64-hex>

Git reads use GIT_OPTIONAL_LOCKS=0. The SQLite ledger is opened only as a
binary file to check its header, not through sqlite3 (no WAL/SHM creation or
DB write). No network, port binding, cert/key/Bearer reads, or Git changes.
Results intentionally never include origin URLs, paths, secrets or raw errors.
Even a complete local pass returns MANUAL_REQUIRED and a nonzero exit code:
actual running listener, ledger integrity/backup/rollback, owner ACL, TLS
negative probes, immutable source/Gateway release and owner GO are separate.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


SHA = re.compile(r'[0-9a-f]{40}\Z')
GENERATION = re.compile(r'[0-9a-f]{64}\Z')
ORIGIN = re.compile(
    r'(?:https://github\.com/|git@github\.com:)'
    r'AlexPastukhh/docke-windows-mirror(?:\.git)?\Z', re.IGNORECASE)
SQLITE_HEADER = b'SQLite format 3\x00'
ALLOWED_REPOS = ('fortis-approvals',)
INVENTORY = ('mirror-pilot', ALLOWED_REPOS)
MANUAL_GATES = (
    'INDEPENDENT_GITHUB_EXACT_MAIN_READBACK',
    'WINDOWS_INSTALLED_SOURCE_AND_PROCESS_IDENTITY',
    'SAME_BROWSER_CORE_AND_SQLITE_LEDGER_PRESERVATION',
    'LEDGER_INTEGRITY_BACKUP_AND_RESTORE_TEST',
    'OWNER_ACL_AND_MACHINE_GENERATION_REVIEW',
    'GATEWAY_INVENTORY_POLICY_AND_GRANT_REVIEW',
    'LISTENER_443_OWNER_CUTOVER_AND_ROLLBACK',
    'GATEWAY_NAMESPACE_CA_SAN_BEARER_HTTP_NEGATIVES',
    'FRESH_TYPED_READ_V2_AND_V1_COMPATIBILITY',
    'SEPARATE_OWNER_GO',
)


def _git_read(root, args):
    # Never fetch, mutate refs/index, authenticate or follow network remotes.
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    proc = subprocess.run(
        ['git', '-c', 'core.fsmonitor=false', '-C', str(root), *args],
        env=env, capture_output=True, text=True, check=False, timeout=8)
    if proc.returncode or len(proc.stdout) > 8192:
        raise OSError('LOCAL_GIT_READ_FAILED')
    return proc.stdout.strip()


def inspect(*, repo_root, userprofile, localappdata, expected_source_sha,
            machine_generation=None, git_reader=None):
    """Run only bounded local checks; all failures use fixed, non-secret codes."""
    checks = []

    def check(label, condition, code):
        checks.append({'check': label, 'state': 'PASS' if condition else 'STOP',
                       'code': None if condition else code})
        return condition

    source_sha_ok = type(expected_source_sha) is str and bool(SHA.fullmatch(expected_source_sha))
    check('expectedSourceSha', source_sha_ok, 'SOURCE_SHA_PIN_REQUIRED')

    generation_ok = type(machine_generation) is str and bool(
        GENERATION.fullmatch(machine_generation))
    check('machineGenerationSyntax', generation_ok,
          'OWNER_REVIEWED_MACHINE_GENERATION_REQUIRED')

    root = Path(repo_root) if repo_root is not None else None
    profile = Path(userprofile) if userprofile else None
    root_ok = (root is not None and profile is not None and
               root.is_absolute() and profile.is_absolute() and
               root.is_dir() and not root.is_symlink() and
               (root / '.git').is_dir() and not (root / '.git').is_symlink() and
               root.resolve() == (profile / 'Repos' / 'docke-windows-mirror').resolve() and
               root == profile / 'Repos' / 'docke-windows-mirror')
    if not check('ownerPrimaryClone', root_ok, 'WINDOWS_PRIMARY_CLONE_UNVERIFIED'):
        return _result(checks)

    git = _git_read if git_reader is None else git_reader
    commands = (
        ('gitRoot', ['rev-parse', '--show-toplevel']),
        ('branch', ['symbolic-ref', '--quiet', '--short', 'HEAD']),
        ('origin', ['remote', 'get-url', 'origin']),
        ('head', ['rev-parse', '--verify', 'HEAD']),
        ('trackingMain', ['rev-parse', '--verify', 'refs/remotes/origin/main']),
        ('status', ['status', '--porcelain=v1', '--untracked-files=all']),
    )
    values = {}
    try:
        for name, args in commands:
            values[name] = git(root, args)
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError):
        check('localGitReads', False, 'LOCAL_GIT_READ_UNAVAILABLE')
        return _result(checks)

    check('gitRoot', (Path(values['gitRoot']).resolve() == root.resolve()),
          'GIT_TOPLEVEL_MISMATCH')
    check('mainBranch', values['branch'] == 'main', 'SOURCE_NOT_MAIN')
    check('originIdentity', bool(ORIGIN.fullmatch(values['origin'])),
          'SOURCE_ORIGIN_DENIED')
    head_ok = bool(SHA.fullmatch(values['head']))
    check('exactSourceSha', head_ok and source_sha_ok and
          values['head'] == expected_source_sha, 'EXACT_SOURCE_SHA_MISMATCH')
    check('trackingMain', head_ok and bool(SHA.fullmatch(values['trackingMain'])) and
          values['trackingMain'] == values['head'],
          'TRACKING_MAIN_NOT_EQUAL')
    check('cleanCheckout', values['status'] == '', 'WINDOWS_SOURCE_DIRTY')

    # The existing Browser ledger is essential to P3b principal-bound reads.
    # No connection to SQLite: avoid initializing a new store, WAL/SHM or
    # acquiring a lock on the live Browser/worker.
    appdata = Path(localappdata) if localappdata else None
    appdata_ok = (appdata is not None and appdata.is_absolute() and
                  appdata.is_dir() and not appdata.is_symlink() and
                  (appdata / 'FortisMirror').is_dir() and
                  not (appdata / 'FortisMirror').is_symlink())
    if check('ownerLedgerRoot', appdata_ok, 'OWNER_LEDGER_ROOT_UNVERIFIED'):
        ledger = appdata / 'FortisMirror' / 'pull-receipts.sqlite3'
        header_ok = False
        try:
            if ledger.is_file() and not ledger.is_symlink() and ledger.stat().st_size >= 100:
                with ledger.open('rb') as inp:
                    header_ok = inp.read(16) == SQLITE_HEADER
        except (OSError, ValueError):
            pass
        check('existingLedgerHeader', header_ok, 'LEDGER_MISSING_OR_INVALID')
    # The header is NOT an integrity check or guarantee about backups/ACLs.
    return _result(checks)


def _result(checks):
    return {
        'schemaVersion': 1,
        'state': 'STOP' if any(c['state'] == 'STOP' for c in checks) else 'MANUAL_REQUIRED',
        'cutoverAuthorized': False,
        'scope': {'inventory': INVENTORY[0], 'repositories': list(INVENTORY[1]),
                  'state': 'SOURCE_EXPECTATION_ONLY'},
        'checks': checks,
        'manualGates': list(MANUAL_GATES),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-source-sha', required=True)
    parser.add_argument('--machine-generation')
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        result = _result([{'check':'operatingSystem','state':'STOP',
                           'code':'WINDOWS_OWNER_REQUIRED'}])
    else:
        result = inspect(
            repo_root=Path(__file__).resolve().parent.parent,
            userprofile=os.environ.get('USERPROFILE'),
            localappdata=os.environ.get('LOCALAPPDATA'),
            expected_source_sha=args.expected_source_sha,
            machine_generation=args.machine_generation)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True,
                     separators=(',', ':')))
    # Intentional: even all local checks passing NEVER acts as an owner GO.
    return 2 if result['state'] == 'STOP' else 3


if __name__ == '__main__':
    raise SystemExit(main())
