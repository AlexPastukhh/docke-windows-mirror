"""Fixture-only P4 process death helper, invoked by hosted Windows unittest.

Not a production CLI or listener. Reads a test-only JSON fixture on stdin;
never receives private signing keys or owner credentials. Does not perform Git.
The caller bounds the process lifetime; exit codes pinpoint durable checkpoints.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from mirror_api.core import MirrorCore
from mirror_api.proof_v2 import PinnedEd25519Keys
from mirror_api.read_v2 import CoreReadV2
from mirror_api.write_v2_bridge import PrivateWriteBridge
from mirror_api.write_v2_ingress import WriteIngressProfile
from test_mirror_atomic_reservation import GEN, REPO, TIME
from test_mirror_machine_reads import BoundBackend
from test_mirror_write_v2_bridge import READ, WRITE, PATH

BEFORE_CLAIM = 71
AFTER_CLAIM = 72
LOST_ACK = 73


def child():
    fixture = json.load(sys.stdin)
    assert type(fixture) is dict and set(fixture) == {
        'mode', 'windowsRoot', 'receiptDb', 'ownerPublicKey', 'dispatch'}
    mode = fixture['mode']
    assert mode in ('before-claim', 'after-claim', 'lost-ack')
    # Refuse any non-temp or real Windows/Docker checkout: this helper must
    # never become an accidental executable for installed Mirror repos.
    temp_root = Path(tempfile.gettempdir()).resolve()
    fixture_root = Path(fixture['windowsRoot']).resolve()
    receipt_db = Path(fixture['receiptDb']).resolve()
    assert fixture_root.name == 'Repos' and fixture_root.is_dir()
    assert fixture_root.parent == receipt_db.parent
    assert temp_root in receipt_db.parents and receipt_db.is_file()
    backend = BoundBackend()
    backend.windows_root = fixture_root
    core = MirrorCore(backend, receipt_db)
    pins = PinnedEd25519Keys(
        {'fixture': bytes.fromhex(fixture['ownerPublicKey'])},
        owner_epoch='e' * 64)
    reads = CoreReadV2(core, allowed_repos=[REPO],
                       inventories={'mirror-pilot': [REPO]},
                       machine_generation='c' * 64,
                       owner_signing_pins=pins)
    bridge = PrivateWriteBridge(
        ingress=WriteIngressProfile(
            read_bearer=READ, write_bearer=WRITE, allowed_repos=[REPO],
            signing_pins=pins),
        reads=reads, gateway_generation=GEN, source_enabled=True)
    worker = bridge._machine.worker
    bridge._machine.clock = lambda: TIME
    raw = json.dumps(fixture['dispatch'], sort_keys=True,
                     separators=(',', ':'), ensure_ascii=True).encode('ascii')
    headers = [('Host', 'host.docker.internal:443'),
               ('Authorization', 'Bearer ' + WRITE),
               ('Content-Type', 'application/json'),
               ('Content-Length', str(len(raw)))]

    def terminate(*_args, **_kwargs):
        os._exit({'before-claim': BEFORE_CLAIM,
                  'after-claim': AFTER_CLAIM}[mode])

    if mode == 'before-claim':
        # Trigger OS-level process termination AFTER atomic signed reservation
        # but BEFORE a durable worker claim or any Git child.
        with patch.object(worker, '_run_machine_reserved', side_effect=terminate):
            bridge.process_envelope(method='POST', path=PATH,
                                    headers=headers, body=raw)
    elif mode == 'after-claim':
        # The real worker commits MACHINE_CLAIMED and acquires the guard first.
        # Its Git implementation is replaced by termination (no real Git).
        with patch.object(worker, '_execute', side_effect=terminate):
            bridge.process_envelope(method='POST', path=PATH,
                                    headers=headers, body=raw)
    else:
        # Terminal receipt is committed before the test child vanishes; caller
        # loses ACK despite an already saved EXECUTED result.
        with patch.object(worker, '_execute',
                          return_value={'requestId': fixture['dispatch']['requestId'],
                                        'state': 'EXECUTED',
                                        'head': fixture['dispatch']['targetSha']}):
            receipt = bridge.process_envelope(method='POST', path=PATH,
                                              headers=headers, body=raw)
        assert receipt['workerState'] == 'EXECUTED'
        os._exit(LOST_ACK)
    raise AssertionError('Fixture child unexpectedly survived effect checkpoint')


if __name__ == '__main__':
    child()
