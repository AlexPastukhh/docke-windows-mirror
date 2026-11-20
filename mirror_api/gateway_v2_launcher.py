"""Explicit owner-window Browser + READ v1/v2 launcher (not a release GO).

Source-only candidate: NEVER attach to an existing Browser or spawn a second
443 listener automatically. The manual owner cutover/rollback plan must first
qualify runtime identity, SQLite continuity, ACL, credential and quiescence.
"""
import argparse
import os
from pathlib import Path
import re
import sys
import threading

from .gateway_readonly import private_file
from .gateway_v2 import create_server
from .operations import Mirror
from .server import MirrorServer, open_local_browser

PILOT_REPOS = ('fortis-approvals',)
PILOT_INVENTORIES = {'mirror-pilot': ('fortis-approvals',)}
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
SQLITE_HEADER = b'SQLite format 3\x00'


class LaunchStop(ValueError):
    """A fixed, public failure code; never include credential or disk paths."""


def check(condition, code):
    if not condition:
        raise LaunchStop(code)


def existing_default_ledger():
    """Only verify the default owner's existing file, never open sqlite3."""
    value = os.environ.get('LOCALAPPDATA')
    check(type(value) is str and bool(value), 'MIRROR_V2_LEDGER_ENV_REQUIRED')
    root = Path(value)
    directory = root / 'FortisMirror'
    ledger = directory / 'pull-receipts.sqlite3'
    check(root.is_absolute() and root.is_dir() and not root.is_symlink() and
          directory.is_dir() and not directory.is_symlink() and
          ledger.is_file() and not ledger.is_symlink(),
          'MIRROR_V2_DEFAULT_LEDGER_REQUIRED')
    try:
        if ledger.stat().st_size < 100:
            raise OSError('undersized')
        # This is only a header check, NOT integrity, live handle binding or
        # evidence of quiescence. Do not create a connection, journal or WAL.
        with ledger.open('rb') as stream:
            check(stream.read(16) == SQLITE_HEADER,
                  'MIRROR_V2_LEDGER_HEADER_INVALID')
    except OSError:
        raise LaunchStop('MIRROR_V2_LEDGER_UNREADABLE') from None
    return ledger


def validate(args):
    """Fail before binding either the Browser or TLS listener."""
    check(sys.platform == 'win32', 'MIRROR_V2_WINDOWS_REQUIRED')
    check(1024 <= args.port <= 65535 and args.port != 443,
          'MIRROR_V2_BROWSER_PORT_DENIED')
    check(type(args.machine_generation) is str and
          bool(HEX64.fullmatch(args.machine_generation)),
          'MIRROR_V2_OWNER_GENERATION_REQUIRED')
    # No scope, HTTPS port, SSL trust or ledger override comes from callers.
    check(type(args.windows_root) is str and
          Path(args.windows_root).is_absolute() and
          Path(args.windows_root).is_dir(),
          'MIRROR_V2_WINDOWS_ROOT_REQUIRED')
    for value in (args.cert, args.key, args.secret_file):
        try:
            private_file(value)
        except (ValueError, TypeError, OSError):
            raise LaunchStop('MIRROR_V2_PRIVATE_FILES_REQUIRED') from None
    existing_default_ledger()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows-root', default=str(Path.home() / 'Repos'))
    parser.add_argument('--port', type=int, default=8765,
                        help='Owner browser loopback port; cannot be TLS/443')
    parser.add_argument('--cert', required=True, help='Owner protected leaf PEM path')
    parser.add_argument('--key', required=True, help='Owner protected leaf key path')
    parser.add_argument('--secret-file', required=True, help='Existing READ Bearer file path')
    parser.add_argument('--machine-generation', required=True,
                        help='Owner-approved public 64-hex generation, never a token')
    parser.add_argument('--open-browser', action='store_true')
    args = parser.parse_args(argv)
    try:
        validate(args)
    except LaunchStop as exc:
        print(str(exc), file=sys.stderr)
        return 2

    browser = external = worker_thread = None
    try:
        # The owner Browser remains the *only* Core owner. No new receipt DB,
        # WriterGuard or worker is constructed by the HTTPS GET adapter.
        browser = MirrorServer(('127.0.0.1', args.port), Mirror(args.windows_root),
                               allow_browser_pull=True, receipt_db=None)
        external = create_server(browser.core, bind='127.0.0.1',
                                 cert=args.cert, key=args.key,
                                 secret_file=args.secret_file,
                                 allowed_repos=PILOT_REPOS,
                                 inventories=PILOT_INVENTORIES,
                                 machine_generation=args.machine_generation)
        worker_thread = threading.Thread(target=external.serve_forever,
                                         daemon=True)
        worker_thread.start()
        # This is socket startup, not owner GO, Gateway grant or TLS acceptance.
        print('MIRROR_READ_V2_SOURCE_LISTENER_STARTED_OWNER_ACCEPTANCE_REQUIRED',
              flush=True)
        print(f'Open locally: http://127.0.0.1:{args.port}/session?boot={browser.bootstrap_token}',
              flush=True)
        if args.open_browser:
            open_local_browser(browser.server_port, browser.bootstrap_token)
        browser.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError):
        # Includes port 443 occupied: never stop/replace the existing v1 server.
        print('MIRROR_V2_LAUNCH_FAILED_NO_CUTOVER', file=sys.stderr)
        return 2
    finally:
        if external is not None:
            try:
                if worker_thread is not None and worker_thread.is_alive():
                    external.shutdown()
            finally:
                external.server_close()
        if browser is not None:
            browser.server_close()  # Browser's one Core is closed exactly once.


if __name__ == '__main__':
    raise SystemExit(main())
