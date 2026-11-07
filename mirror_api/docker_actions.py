"""Bounded owner-only Docker Git actions. Not exposed to Gateway or AI.

The native-Gateway repositories must NOT be admitted to this legacy UI route.
No local mutation is available until the owner explicitly allows a repository.
"""
import base64
import re
import secrets
import threading
import time
from .operations import CONTAINER, REPO, SHA, ObservationError, run

ACTIONS = '/workspace/repo/Repos/docke-windows-mirror/tools/Docker-Mirror-Git-Actions.py'
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
BRANCH = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}\Z')


class ActionDenied(ValueError):
    pass


def validated(value, pattern):
    return type(value) is str and bool(pattern.fullmatch(value)) and value not in ('.','..')


class DockerActions:
    def __init__(self, allowed):
        self.allowed = frozenset(allowed)
        self.previews = {}
        self.lock = threading.Lock()

    def invoke(self, repo, task, *flags):
        if repo not in self.allowed or not validated(repo, REPO) or not validated(task, REPO):
            raise ActionDenied('DOCKER_ACTION_TARGET_NOT_ALLOWED')
        return run(['docker','exec','--user','1000:1000',CONTAINER,'python3','-B',
                    ACTIONS,'--repo',repo,'--task',task,*flags],timeout=150)

    def _save(self, owner_session,repo,task,operation,header,files):
        token=secrets.token_urlsafe(32)
        self.previews[token]={'owner':owner_session,'repo':repo,'task':task,
                              'operation':operation,'header':header,
                              'expires':time.monotonic()+300,'files':files}
        return {'previewId':token,'repoId':repo,'task':task,'operation':operation,
                'branch':header[1],'head':header[2],
                'digest':header[3], 'files':files}

    def preview_checked(self, owner_session,repo,task,operation):
        if operation not in ('stage','commit','push'):
            raise ActionDenied('UNKNOWN_DOCKER_ACTION')
        raw=self.invoke(repo,task,'--'+operation+'-preview')
        lines=raw.splitlines()
        if not lines:
            raise ActionDenied('DOCKER_PREVIEW_EMPTY')
        header=lines.pop(0).split('\t')
        if (len(header)!=(4 if operation=='push' else 5) or
                header[0]!='FORTIS_DOCKER_'+operation.upper()+'_V1' or
                not validated(header[1],BRANCH) or not validated(header[2],SHA) or
                not validated(header[3],SHA if operation=='push' else DIGEST)):
            raise ActionDenied('DOCKER_PREVIEW_INVALID')
        files=[]
        if operation!='push':
            try:
                count=int(header[4])
                if count<0 or count>500 or count!=len(lines):
                    raise ValueError()
                for index,line in enumerate(lines):
                    parts=line.split('\t')
                    if operation=='stage':
                        if len(parts)!=3 or parts[:2]!=['F',str(index)]:
                            raise ValueError()
                        content=parts[2]
                    else:
                        if len(parts)!=2 or parts[0]!='F':
                            raise ValueError()
                        content=parts[1]
                    files.append(base64.b64decode(content,validate=True).decode('utf-8'))
            except (ValueError,UnicodeError):
                raise ActionDenied('DOCKER_PREVIEW_INVALID') from None
        elif lines:
            raise ActionDenied('DOCKER_PREVIEW_INVALID')
        return self._save(owner_session,repo,task,operation,header,files)

    def execute(self, owner_session, preview_id, options):
        if type(options) is not dict or type(preview_id) is not str:
            raise ActionDenied('ACTION_INPUT_INVALID')
        with self.lock:
            preview=self.previews.pop(preview_id,None)
            if not preview or preview['owner']!=owner_session or preview['expires']<time.monotonic():
                raise ActionDenied('ACTION_PREVIEW_EXPIRED')
            repo,task,operation=preview['repo'],preview['task'],preview['operation']
            h=preview['header']
            flags=['--'+operation,'--expect-branch',h[1],'--expect-head',h[2]]
            if operation=='stage':
                if set(options)!={'indices'} or type(options['indices']) is not list:
                    raise ActionDenied('ACTION_SELECTION_REQUIRED')
                indices=options['indices']
                if (not indices or len(indices)>500 or len(set(indices))!=len(indices) or
                        any(type(i) is not int or not 0<=i<len(preview['files']) for i in indices)):
                    raise ActionDenied('ACTION_SELECTION_INVALID')
                flags.extend(['--expect-digest',h[3],'--indices',','.join(map(str,indices))])
            elif operation=='commit':
                message=options.get('message') if set(options)=={'message'} else None
                if type(message) is not str or not message.strip() or message!=message.strip() or len(message)>4000:
                    raise ActionDenied('COMMIT_MESSAGE_INVALID')
                flags.extend(['--expect-digest',h[3],'--message-b64',
                              base64.b64encode(message.encode()).decode()])
            else:
                if options:
                    raise ActionDenied('ACTION_INPUT_INVALID')
                flags.extend(['--expect-remote',h[3]])
            result=self.invoke(repo,task,*flags)
            return {'operation':operation,'repoId':repo,'task':task,
                    'result':result,'requiresRefresh':True}
