"""Principal-bound READ metadata in the existing Core worker's SQLite ledger.

No effects or machine binding creation exist here. P4 alone may create a binding
atomically with its signed dispatch reservation. Lookup never adopts old receipts.
"""
from datetime import datetime, timezone
import json
import re
import time
import uuid

from .read_v2 import ReadError, digest, require, source
from .operations import REPO, SHA, ObservationError
from .pull import PullDenied

ACTOR = re.compile(r'[A-Za-z0-9_.:-]{1,120}\Z')
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
GENERATION = re.compile(r'[0-9a-f]{64}\Z')


def principal(value):
    require(type(value) is str and ACTOR.fullmatch(value), 'PERMISSION_DENIED')
    return value


def identifier(value):
    require(type(value) is str and UUID4.fullmatch(value))
    return value


def timestamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='seconds')


def blocker(reason):
    return {'PREVIOUS_PULL_UNRESOLVED': 'OUTCOME_UNCONFIRMED',
            'DOCKER_MAIN_NOT_VERIFIED': 'DOCKER_MAIN_MISMATCH',
            'DOCKER_MAIN_NOT_AT_GITHUB_MAIN': 'DOCKER_MAIN_MISMATCH',
            'DOCKER_ORIGIN_UNVERIFIED': 'ORIGIN_MISMATCH',
            'WINDOWS_ORIGIN_MISMATCH': 'ORIGIN_MISMATCH',
            'WINDOWS_NOT_ON_MAIN': 'WINDOWS_NOT_ON_MAIN',
            'WINDOWS_NOT_CLEAN': 'WINDOWS_NOT_CLEAN',
            'WINDOWS_ALREADY_CURRENT': 'WINDOWS_ALREADY_CURRENT',
            'WINDOWS_NOT_BEHIND_GITHUB': 'NOT_FAST_FORWARD',
            'GITHUB_MAIN_UNVERIFIED': 'GITHUB_MAIN_UNVERIFIED',
            'MIRROR_WRITER_BUSY': 'WRITER_BUSY'}.get(reason, 'SOURCE_UNAVAILABLE')


class MachineReads:
    TTL = 120
    MAX_PREVIEWS = 4096
    MAX_ACTIVE_PER_ACTOR = 64

    def __init__(self, core, generation, *, clock=None, signing_pins=None):
        require(type(generation) is str and GENERATION.fullmatch(generation))
        self.core, self.generation, self.clock = core, generation, clock or time.time
        self._signing_pins = signing_pins
        # Exactly the shared Core worker/store. Initializing its ledger binds the
        # store but never claims a writer, invokes status/recovery or starts Git.
        self.worker = core.worker()
        with self.worker._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS machine_previews (
                request_id TEXT PRIMARY KEY, actor TEXT NOT NULL, repo_id TEXT NOT NULL,
                gateway_generation TEXT NOT NULL, config_generation TEXT NOT NULL,
                preview_id TEXT UNIQUE, expires_at INTEGER NOT NULL, origin TEXT, data TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS machine_bindings (
                request_id TEXT PRIMARY KEY, actor TEXT NOT NULL, repo_id TEXT NOT NULL,
                identity TEXT NOT NULL)''')

    def _existing(self, db, request_id, actor, repo, gateway_generation):
        row = db.execute('SELECT actor,repo_id,gateway_generation,config_generation,data '
                         'FROM machine_previews WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            return None
        require(row[0] == actor and row[1] == repo, 'REQUEST_ID_CONFLICT')
        require(row[2] == gateway_generation and row[3] == self._generation(gateway_generation),
                'PREVIEW_STALE')
        return json.loads(row[4])

    def _generation(self, gateway_generation):
        return digest({'windows': self.generation, 'gateway': gateway_generation})

    def _capacity(self, db, actor, request_id, now):
        require(not db.execute('SELECT 1 FROM pull_receipts WHERE request_id=?', (request_id,)).fetchone()
                and not db.execute('SELECT 1 FROM machine_bindings WHERE request_id=?', (request_id,)).fetchone(),
                'REQUEST_ID_CONFLICT')
        require(db.execute('SELECT COUNT(*) FROM machine_previews').fetchone()[0] < self.MAX_PREVIEWS,
                'LIMIT_EXCEEDED')
        require(db.execute('SELECT COUNT(*) FROM machine_previews WHERE actor=? AND expires_at>?',
                           (actor, now)).fetchone()[0] < self.MAX_ACTIVE_PER_ACTOR, 'LIMIT_EXCEEDED')

    def preview(self, repo, actor, request_id, gateway_generation):
        principal(actor); identifier(request_id)
        require(type(gateway_generation) is str and GENERATION.fullmatch(gateway_generation))
        with self.worker._db() as db:
            existing = self._existing(db, request_id, actor, repo, gateway_generation)
            if existing is not None:
                return existing
            self._capacity(db, actor, request_id, int(self.clock()))
        # No SQLite transaction/lock spans the bounded observation.
        facts, row, observed = self.core.preview_observation(repo)
        require(type(row) is dict and row.get('repoId') == repo)
        docker, windows, github = row['docker']['primary'], row['windows'], row['github']
        require(all(value.get('repoId') == repo for value in (docker, windows, github)))
        evidence = {'docker': source(docker, kind='docker', timestamp=observed),
                    'windows': source(windows, kind='windows', timestamp=observed),
                    'github': source(github, kind='github', timestamp=observed)}
        reasons = list(dict.fromkeys(blocker(item) for item in facts['reasons']))
        slug = github.get('githubRepository')
        origin = facts.get('origin')
        identity = ('AlexPastukhh/' + slug if type(slug) is str and REPO.fullmatch(slug) else None)
        if identity is None or type(origin) is not str or not 0 < len(origin) <= 512:
            identity, origin = None, None
            reasons.append('ORIGIN_MISMATCH')
        if any(value['verification'] != 'VERIFIED' for value in evidence.values()):
            reasons.append('SOURCE_UNAVAILABLE')
        now = int(self.clock())
        data = {'repoId': repo, 'previewId': str(uuid.uuid4()) if not reasons else None,
                'expectedHead': facts['windowsHead'], 'targetSha': facts['targetSha'],
                'dockerHead': facts['dockerHead'], 'originIdentity': identity,
                'sourceEvidence': evidence, 'checkedAt': timestamp(now),
                'expiresAt': timestamp(now + self.TTL), 'configGeneration': self._generation(gateway_generation),
                'blockers': list(dict.fromkeys(reasons))}
        require(all(data[key] is None or type(data[key]) is str and SHA.fullmatch(data[key])
                    for key in ('expectedHead', 'targetSha', 'dockerHead')))
        raw = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
        require(len(raw.encode()) <= 8192, 'LIMIT_EXCEEDED')
        with self.worker._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self._existing(db, request_id, actor, repo, gateway_generation)
            if existing is not None:
                return existing
            self._capacity(db, actor, request_id, now)
            db.execute('INSERT INTO machine_previews VALUES(?,?,?,?,?,?,?,?,?)',
                       (request_id, actor, repo, gateway_generation, data['configGeneration'],
                        data['previewId'], now + self.TTL, origin, raw))
        return data

    def persisted_preview_for_ask(self, repo, actor, preview_id, gateway_generation):
        """Private P4 prerequisite: read the immutable READ snapshot by previewId.

        Caller authentication and ASK approval are NOT established by this
        lookup. A future trusted Gateway adapter must prove the actor and
        independently revalidate fresh Git preconditions before signing.
        No HTTP route, TTL renewal, worker reservation or Git observation.
        """
        require(type(repo) is str and REPO.fullmatch(repo), 'REPOSITORY_NOT_ALLOWED')
        principal(actor); identifier(preview_id)
        require(type(gateway_generation) is str and GENERATION.fullmatch(gateway_generation))
        with self.worker._db() as db:
            row = db.execute(
                'SELECT request_id,actor,repo_id,gateway_generation,config_generation,'
                'expires_at,origin,data FROM machine_previews WHERE preview_id=?',
                (preview_id,)).fetchone()
        # Unknown, Browser and foreign snapshots are indistinguishable.
        require(row is not None and row[1] == actor and row[2] == repo,
                'PERMISSION_DENIED')
        require(row[3] == gateway_generation and
                row[4] == self._generation(gateway_generation), 'PREVIEW_STALE')
        require(int(self.clock()) < row[5], 'PREVIEW_EXPIRED')
        snapshot = json.loads(row[7])
        require(type(snapshot) is dict and snapshot.get('repoId') == repo and
                snapshot.get('previewId') == preview_id and
                snapshot.get('configGeneration') == row[4] and
                snapshot.get('expiresAt') == timestamp(row[5]) and
                snapshot.get('blockers') == [] and
                snapshot.get('originIdentity') == 'AlexPastukhh/' + repo and
                type(row[6]) is str and 0 < len(row[6]) <= 512 and
                all(type(snapshot.get(k)) is str and SHA.fullmatch(snapshot[k])
                    for k in ('expectedHead', 'targetSha', 'dockerHead')) and
                snapshot['expectedHead'] != snapshot['targetSha'] and
                snapshot['dockerHead'] == snapshot['targetSha'] and
                type(snapshot.get('sourceEvidence')) is dict and
                set(snapshot['sourceEvidence']) == {'windows', 'docker', 'github'} and
                all(type(item) is dict and item.get('verification') == 'VERIFIED'
                    and item.get('branch') == 'main'
                    for item in snapshot['sourceEvidence'].values()) and
                snapshot['sourceEvidence']['windows'].get('head') == snapshot['expectedHead'] and
                snapshot['sourceEvidence']['github'].get('head') == snapshot['targetSha'] and
                snapshot['sourceEvidence']['docker'].get('head') == snapshot['targetSha'] and
                snapshot['sourceEvidence']['windows'].get('clean') is True and
                snapshot['sourceEvidence']['docker'].get('clean') is True,
                'REQUEST_ID_CONFLICT')
        # Private origin is deliberately NOT part of the public READ envelope.
        return {'readRequestId': row[0], 'origin': row[6], 'snapshot': snapshot}

    def _fresh_signed_preconditions(self, repo, stored):
        """Bounded read-only semantic revalidation before a new reservation.

        Ignore volatile timestamps/timings; never reissue a preview, change its
        target or TTL, acquire the writer guard, or use a cached GitHub main.
        The worker must independently recheck before any Git effect.
        """
        original = stored['snapshot']
        try:
            facts, row, observed = self.core.preview_observation(repo)
        except (PullDenied, ObservationError, OSError):
            raise ReadError('SOURCE_UNAVAILABLE') from None
        require(type(facts) is dict and facts.get('repoId') == repo and
                type(facts.get('reasons')) is list, 'PREVIEW_STALE')
        # The common ledger already fences an unresolved Browser/machine
        # writer. Preserve the same stable admission refusal in both paths.
        require('PREVIOUS_PULL_UNRESOLVED' not in facts['reasons'],
                'REPOSITORY_OPERATION_PENDING')
        require(facts.get('available') is True and facts['reasons'] == [],
                'PREVIEW_STALE')
        require(type(row) is dict and row.get('repoId') == repo,
                'PREVIEW_STALE')
        require(facts.get('origin') == stored['origin'] and
                facts.get('windowsHead') == original['expectedHead'] and
                facts.get('targetSha') == original['targetSha'] and
                facts.get('dockerHead') == original['dockerHead'],
                'PREVIEW_STALE')
        docker = row.get('docker', {}).get('primary') or {}
        windows = row.get('windows') or {}
        github = row.get('github') or {}
        require(all(type(item) is dict and item.get('repoId') == repo
                    for item in (docker, windows, github)), 'PREVIEW_STALE')
        require(docker.get('kind') == 'primary' and
                docker.get('status') == 'registered' and
                docker.get('targetEligible') is True and
                docker.get('origin') == stored['origin'] and
                windows.get('exists') is True and
                windows.get('originValid') is True and
                windows.get('origin') == stored['origin'] and
                github.get('githubRepository') == repo, 'PREVIEW_STALE')
        refreshed = {
            'docker': source(docker, kind='docker', timestamp=observed),
            'windows': source(windows, kind='windows', timestamp=observed),
            'github': source(github, kind='github', timestamp=observed),
        }
        # Semantics must not change between preview and ASK. New observation
        # timestamps are expected and are deliberately not equality inputs.
        semantic = ('verification', 'head', 'branch', 'clean', 'errorCode')
        require(all(
            refreshed[name]['verification'] == 'VERIFIED' and
            all(refreshed[name].get(field) == original['sourceEvidence'][name].get(field)
                for field in semantic)
            for name in ('docker', 'windows', 'github')), 'PREVIEW_STALE')

    def reserve_signed_candidate(self, *, dispatch, actor, repo, request_id,
                                 gateway_generation, owner_pins, _handoff_permit=None):
        """P4 source-only candidate: one atomic binding + receipt reservation.

        NOT a public operation or an execution handoff. Caller MUST authenticate
        actor and WRITE bearer independently and check fresh Git/policy before
        making this call. Reserved rows deliberately block all future writers;
        only a separately reviewed, single-use worker handoff may execute them.
        A matching old UUID is lookup-only, including after proof/preview TTL.
        """
        from .proof_v2 import OUTER, verify_new_dispatch

        principal(actor); identifier(request_id)
        require(type(repo) is str and REPO.fullmatch(repo), 'REPOSITORY_NOT_ALLOWED')
        require(type(gateway_generation) is str and GENERATION.fullmatch(gateway_generation))
        require(type(dispatch) is dict and set(dispatch) == OUTER, 'SCHEMA_INVALID')
        require(dispatch['requestId'] == request_id and dispatch['repoId'] == repo and
                dispatch['actor'] == actor, 'PERMISSION_DENIED')
        require(type(dispatch['proof']) is dict and
                set(dispatch['proof']) == {'schemaVersion', 'keyId', 'claims', 'signature'},
                'PROOF_REQUIRED')
        require(type(dispatch['previewId']) is str and UUID4.fullmatch(dispatch['previewId']) and
                all(type(dispatch[k]) is str and SHA.fullmatch(dispatch[k])
                    for k in ('expectedHead', 'targetSha')) and
                type(dispatch['configGeneration']) is str and
                GENERATION.fullmatch(dispatch['configGeneration']), 'SCHEMA_INVALID')
        identity_keys = ('previewId', 'expectedHead', 'targetSha', 'configGeneration')
        identity = {k: dispatch[k] for k in identity_keys}
        frozen_identity = json.dumps(identity, sort_keys=True, separators=(',', ':'))

        def existing(db):
            # Status-only replay; no approval/signature/expiry refresh. Compare
            # both durable identities, refuse all Browser or foreign UUIDs.
            bound = db.execute('SELECT actor,repo_id,identity FROM machine_bindings '
                               'WHERE request_id=?', (request_id,)).fetchone()
            receipt = db.execute('SELECT payload FROM pull_receipts WHERE request_id=?',
                                 (request_id,)).fetchone()
            if bound is None and receipt is None:
                return None
            require(bound is not None and receipt is not None, 'PERMISSION_DENIED')
            require(bound[0] == actor and bound[1] == repo, 'PERMISSION_DENIED')
            saved_preview = db.execute(
                'SELECT origin FROM machine_previews WHERE preview_id=? AND actor=? AND repo_id=?',
                (identity['previewId'], actor, repo)).fetchone()
            require(saved_preview is not None, 'PERMISSION_DENIED')
            expected = dict(requestId=request_id, repoId=repo,
                            expectedHead=identity['expectedHead'],
                            targetSha=identity['targetSha'], origin=saved_preview[0])
            frozen_payload = json.dumps(expected, sort_keys=True, separators=(',', ':'))
            require(bound[2] == frozen_identity and receipt[0] == frozen_payload,
                    'REQUEST_ID_CONFLICT')
            return {'requestId': request_id, 'reservation': 'EXISTING',
                    'dispatchReady': False}

        # Existing machine UUID must not be proof-reverified/redispatched after
        # TTL. This check is inside the same transaction as initial admission.
        with self.worker._db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = existing(db)
            if prior is not None:
                return prior

        # New effects require the exact owner-bound signing profile whose
        # revision was hashed into this machine's preview generation. Incoming
        # key lists and a different instance with equal public keys are never
        # authority. Existing UUID lookup above remains possible after rotation.
        require(self._signing_pins is not None and
                owner_pins is self._signing_pins and
                owner_pins.revision is not None, 'UNKNOWN_KEY')
        # No broad SQLite transaction spans cryptographic verification.
        # The row is rechecked for exact equality *inside* the write transaction.
        stored = self.persisted_preview_for_ask(repo, actor, identity['previewId'],
                                                gateway_generation)
        verified = verify_new_dispatch(
            dispatch, authenticated_actor=actor, route_repo=repo,
            route_request_id=request_id, persisted_preview=stored,
            owner_pins=owner_pins, now=int(self.clock()))
        try:
            self._fresh_signed_preconditions(repo, stored)
        except ReadError:
            # A parallel identical request may have committed the durable
            # reservation during fresh observation. It is lookup-only, never
            # a second proof/preview refresh or a second writer.
            with self.worker._db() as db:
                db.execute('BEGIN IMMEDIATE')
                prior = existing(db)
                if prior is not None:
                    return prior
            raise
        expected = dict(requestId=request_id, repoId=repo,
                        expectedHead=verified.expected_head,
                        targetSha=verified.target_sha, origin=verified.origin)
        frozen_payload = json.dumps(expected, sort_keys=True, separators=(',', ':'))
        with self.worker._db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Fail closed if a concurrent writer reserved this UUID while
            # signature verification was in progress. Never adopt Browser UUIDs.
            prior = existing(db)
            if prior is not None:
                return prior
            require(not db.execute('SELECT 1 FROM machine_previews WHERE request_id=?',
                                   (request_id,)).fetchone(), 'REQUEST_ID_CONFLICT')
            now = int(self.clock())
            require(now < verified.expires_at, 'PREVIEW_EXPIRED')
            row = db.execute('SELECT request_id,actor,repo_id,gateway_generation,'
                             'config_generation,expires_at,origin,data FROM machine_previews '
                             'WHERE preview_id=?', (verified.preview_id,)).fetchone()
            require(row is not None and row[0] == verified.read_request_id and
                    row[1] == actor and row[2] == repo and
                    row[3] == gateway_generation and
                    row[4] == self._generation(gateway_generation) and
                    row[5] > now and row[6] == verified.origin and
                    json.loads(row[7]) == stored['snapshot'], 'PREVIEW_STALE')
            blocker = self.worker._reservation_blocker(db, repo)
            require(blocker is None, blocker or 'REPOSITORY_OPERATION_PENDING')
            progress = {'requestId': request_id, 'state': 'DISPATCHED', 'head': None,
                        'phase': 'RESERVED_MACHINE', 'mergeStarted': False}
            # These inserts commit or roll back as ONE unit in the worker DB.
            db.execute('INSERT INTO machine_bindings VALUES(?,?,?,?)',
                       (request_id, actor, repo, frozen_identity))
            db.execute('INSERT INTO pull_receipts VALUES(?,?,?,?)',
                       (request_id, frozen_payload, 'DISPATCHED',
                        json.dumps(progress, sort_keys=True)))
        if _handoff_permit is not None:
            self.worker._arm_machine_handoff(request_id, _handoff_permit,
                                             verified.expires_at)
        return {'requestId': request_id, 'reservation': 'RESERVED',
                'dispatchReady': False}

    def dispatch_signed_candidate(self, *, dispatch, actor, repo, request_id,
                                  gateway_generation, owner_pins):
        """PRIVATE source-only synchronous one-use signed machine handoff.

        This MUST NOT be connected to HTTP until READ, WRITE identity/pins,
        credential lifecycle, admission and Windows crash qualification pass.
        The only execution-capable path requires a just-created reservation;
        EXISTING means read-only lookup, even after a crash/lost response.
        """
        # Keep in-process Browser and machine reservation/execution serialized.
        # Across processes the SQLite BEGIN IMMEDIATE and WriterGuard remain
        # the authoritative persistence and Git exclusion barriers.
        principal(actor)
        identifier(request_id)
        require(type(repo) is str and REPO.fullmatch(repo), 'REPOSITORY_NOT_ALLOWED')
        lock = self.worker._repo_lock(repo)
        if not lock.acquire(blocking=False):
            raise ReadError('REPOSITORY_OPERATION_PENDING')
        try:
            permit = object()
            result = self.reserve_signed_candidate(
                dispatch=dispatch, actor=actor, repo=repo,
                request_id=request_id, gateway_generation=gateway_generation,
                owner_pins=owner_pins, _handoff_permit=permit)
            if result['reservation'] != 'RESERVED':
                return self.lookup(repo, actor, request_id)
            identity = {key: dispatch[key] for key in
                        ('previewId','expectedHead','targetSha','configGeneration')}
            self.worker._run_machine_reserved(
                request_id=request_id, repo_id=repo, actor=actor,
                identity=identity, now=int(self.clock()), permit=permit)
            return self.lookup(repo, actor, request_id)
        finally:
            self.worker._discard_machine_handoff(request_id, permit)
            lock.release()

    def lookup(self, repo, actor, request_id):
        principal(actor); identifier(request_id)
        with self.worker._db() as db:
            binding = db.execute('SELECT identity FROM machine_bindings WHERE request_id=? AND actor=? AND repo_id=?',
                                 (request_id, actor, repo)).fetchone()
            # Same response for absent, Browser-only and another actor's UUID.
            require(binding is not None, 'PERMISSION_DENIED')
            identity = json.loads(binding[0])
            require(type(identity) is dict and set(identity) ==
                    {'previewId', 'expectedHead', 'targetSha', 'configGeneration'})
            identifier(identity['previewId'])
            require(type(identity['configGeneration']) is str and GENERATION.fullmatch(identity['configGeneration']))
            require(all(type(identity[key]) is str and SHA.fullmatch(identity[key])
                        for key in ('expectedHead', 'targetSha')))
            saved = db.execute('SELECT data,origin FROM machine_previews WHERE preview_id=? AND actor=? AND repo_id=?',
                               (identity['previewId'], actor, repo)).fetchone()
            require(saved is not None, 'PERMISSION_DENIED')
            preview = json.loads(saved[0])
            require(all(identity[key] == preview[key] for key in identity), 'REQUEST_ID_CONFLICT')
            stored = db.execute('SELECT payload,state,result FROM pull_receipts WHERE request_id=?', (request_id,)).fetchone()
        target = identity['targetSha']
        result = {'requestId': request_id, 'repoId': repo, 'workerState': 'NOT_FOUND',
                  'effect': 'UNKNOWN', 'observedHead': None, 'expectedTarget': target,
                  'checkedAt': timestamp(int(self.clock())), 'errorCode': 'OUTCOME_UNCONFIRMED', 'evidence': 'NONE'}
        if stored is None:
            return result
        payload, state, raw = stored
        expected = dict(requestId=request_id, repoId=repo, expectedHead=identity['expectedHead'],
                        targetSha=target, origin=saved[1])
        require(json.loads(payload) == expected, 'REQUEST_ID_CONFLICT')
        receipt = json.loads(raw) if raw else {}
        require(type(receipt) is dict)
        if receipt:
            require(receipt.get('requestId') == request_id and receipt.get('state') == state)
        head = receipt.get('head')
        require(head is None or type(head) is str and SHA.fullmatch(head))
        result.update(workerState=state if state in ('DISPATCHED', 'UNCERTAIN') else 'UNCERTAIN', observedHead=head)
        # This READ observes stored outcomes only. It never calls worker.status,
        # probes Git or infers completion from an intermediate target readback.
        if state == 'EXECUTED' and head == target and receipt.get('workerFinished') is not False:
            result.update(workerState='EXECUTED', effect='CONFIRMED', errorCode=None,
                          evidence='TARGET_READBACK' if receipt.get('evidence') == 'TARGET_READBACK' else 'WORKER_RECEIPT')
        elif (state == 'FAILED' and receipt.get('knownFailure') is True and
              receipt.get('mergeStarted') is False and receipt.get('workerFinished') is True):
            result.update(workerState='FAILED', effect='NONE', errorCode=blocker(receipt.get('error')))
        return result
