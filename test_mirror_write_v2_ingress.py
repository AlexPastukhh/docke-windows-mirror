"""P4 disabled WRITE HTTP-envelope tests. Nothing listens or writes Git."""
import copy
from dataclasses import FrozenInstanceError
import json
import unittest
from unittest.mock import patch

from mirror_api.gateway_v2 import ReadV2Handler, route
from mirror_api.gateway_readonly import ReadOnlyHandler
from mirror_api.proof_v2 import PinnedEd25519Keys
from mirror_api.read_v2 import ReadError
from mirror_api.write_v2_ingress import WriteIngressProfile, UntrustedWriteCandidate, MAX_BODY

READ = 'R' * 44
WRITE = 'W' * 44
REPO = 'fortis-approvals'
_UNSET = object()
PATH = f'/gateway/v2/repositories/{REPO}/pull'
RID = '2166d435-e811-41d3-a8c8-c7d807942c25'
PREVIEW = '0ec61e55-b834-47b7-8e6c-b8027553a4bf'


def candidate():
    fields = dict(requestId=RID, repoId=REPO, actor='repo:fixture',
                  previewId=PREVIEW, expectedHead='b' * 40,
                  targetSha='a' * 40, configGeneration='d' * 64)
    claims = dict(**fields, issuer='fortis-gateway', audience='fortis-mirror',
                  service='mirror', operation='execute_windows_pull_v2',
                  fingerprint='e' * 64, issuedAt=1794877200,
                  expiresAt=1794877300)
    return dict(**fields, proof=dict(schemaVersion=1, keyId='owner-key',
                                      claims=claims, signature='A' * 86))


def serialize(value):
    # Gateway JSONAPI uses the Core canonical format: sort keys, compact.
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


class OfflineWriteIngressTests(unittest.TestCase):
    def setUp(self):
        pins = PinnedEd25519Keys({'owner-key': b'\x04' * 32}, owner_epoch='e' * 64)
        self.profile = WriteIngressProfile(read_bearer=READ, write_bearer=WRITE,
                                           allowed_repos=[REPO], signing_pins=pins)
        self.body = serialize(candidate())
        self.headers = [('Host', 'host.docker.internal:443'),
                        ('Authorization', 'Bearer ' + WRITE),
                        ('Content-Type', 'application/json'),
                        ('Content-Length', str(len(self.body))),
                        ('Accept', 'application/json')]

    def accept(self, *, headers=None, body=_UNSET, path=PATH, method='POST'):
        return self.profile.accept(method, path,
                                   self.headers if headers is None else headers,
                                   self.body if body is _UNSET else body)

    def test_exact_write_service_bearer_accepts_untrusted_shape_only(self):
        with patch('mirror_api.proof_v2._verify_ed25519') as crypto:
            result = self.accept()
        self.assertIsInstance(result, UntrustedWriteCandidate)
        self.assertEqual(result.repo_id, REPO)
        self.assertEqual(result.request_id, RID)
        self.assertEqual(result.untrusted_dispatch(), candidate())
        self.assertNotIn('repo:fixture', repr(result))
        self.assertNotIn('signature', repr(result))
        again = result.untrusted_dispatch()
        again['actor'] = 'attacker'
        self.assertEqual(result.untrusted_dispatch()['actor'], 'repo:fixture')
        with self.assertRaises(FrozenInstanceError):
            result.repo_id = 'other'
        crypto.assert_not_called()  # still not actor-verified or ASK-approved

    def test_cannot_enable_listener_or_core_routes_by_instantiating_profile(self):
        self.assertIs(ReadV2Handler.do_POST, ReadOnlyHandler.deny_write)
        with self.assertRaises(ReadError):
            route(PATH)
        self.assertFalse(hasattr(self.profile, 'dispatch_signed_candidate'))
        self.assertFalse(hasattr(self.profile, 'run'))
        self.assertFalse(hasattr(self.profile, 'serve_forever'))

    def test_read_bearer_cannot_authorize_write_even_with_shaped_proof(self):
        for header in ('Bearer ' + READ, 'Bearer wrong', 'Basic ' + WRITE,
                       'Bearer ' + WRITE + 'x', '', 'bearer ' + WRITE):
            with self.subTest(header=header[:10]), self.assertRaisesRegex(ReadError,'PERMISSION_DENIED'):
                headers = [(k, header if k == 'Authorization' else v)
                           for k, v in self.headers]
                self.accept(headers=headers)
        with self.assertRaisesRegex(ReadError,'PERMISSION_DENIED'):
            self.accept(headers=[item for item in self.headers if item[0] != 'Authorization'])

    def test_profile_needs_separate_owner_credentials_pinned_keys_and_scope(self):
        pins = self.profile.signing_pins
        for read, write in ((READ, READ), ('x', WRITE), (READ, 'x'),
                            ('Ä' * 44, WRITE), (READ, 'W' * 301)):
            with self.subTest(read=read[:6],write=write[:6]), self.assertRaises(ReadError):
                WriteIngressProfile(read_bearer=read, write_bearer=write,
                                     allowed_repos=[REPO], signing_pins=pins)
        for scope in ([],[REPO,REPO],['bad name'],[REPO,3],[[REPO]],REPO):
            with self.subTest(scope=scope), self.assertRaises(ReadError):
                WriteIngressProfile(read_bearer=READ,write_bearer=WRITE,
                                     allowed_repos=scope,signing_pins=pins)
        with self.assertRaisesRegex(ReadError,'UNKNOWN_KEY'):
            WriteIngressProfile(read_bearer=READ,write_bearer=WRITE,
                allowed_repos=[REPO],signing_pins=PinnedEd25519Keys(
                    {'owner-key':b'\x04'*32}))
        self.assertNotIn(READ,repr(self.profile))
        self.assertNotIn(WRITE,repr(self.profile))

    def test_exact_path_host_method_denials(self):
        for bad in (PATH+'/',PATH+'?debug=1',PATH+'#frag',
                    PATH.replace(REPO,'other'),PATH.replace('/gateway','//gateway'),
                    PATH.replace('pull','%70ull'),'/gateway/v1/repositories/'+REPO+'/pull-main',
                    PATH.replace('/pull','/pull-preview'),PATH+';x'):
            with self.subTest(path=bad), self.assertRaises(ReadError):
                self.accept(path=bad)
        for method in ('GET','PUT','PATCH','DELETE','post',''):
            with self.subTest(method=method), self.assertRaises(ReadError):
                self.accept(method=method)
        self.assertEqual(self.accept(headers=[
            (k,'host.docker.internal' if k=='Host' else v)
            for k,v in self.headers]).repo_id,REPO)
        for host in ('HOST.DOCKER.INTERNAL:443',
                     'localhost:443','127.0.0.1','host.docker.internal:80'):
            with self.subTest(host=host),self.assertRaisesRegex(ReadError,'PERMISSION_DENIED'):
                self.accept(headers=[(k,host if k=='Host' else v) for k,v in self.headers])

    def test_duplicate_auth_and_ambiguous_header_bypass_denied(self):
        for name,value in (('authorization','Bearer '+WRITE),
                           ('Host','host.docker.internal:443'),
                           ('CONTENT-LENGTH',str(len(self.body))),
                           ('Transfer-Encoding','chunked'),('Cookie','session=x'),
                           ('Origin','https://example.invalid'),('X-CSRF-Token','x'),
                           ('X-Forwarded-Host','example.invalid'),
                           ('Proxy-Authorization','Bearer '+WRITE),
                           ('Expect','100-continue')):
            with self.subTest(name=name),self.assertRaises(ReadError):
                self.accept(headers=self.headers+[(name,value)])
        with self.assertRaises(ReadError):
            self.accept(headers=self.headers+[('User-Agent','x\nInjected: true')])
        with self.assertRaises(ReadError):
            self.accept(headers=self.headers+[(1,'x')])

    def test_no_oversize_chunking_unknown_length_or_body_confusion(self):
        for length in ('0','4097','00010','+10','NaN','-1','1.0',''):
            with self.subTest(length=length),self.assertRaises(ReadError):
                self.accept(headers=[(k,length if k=='Content-Length' else v)
                                     for k,v in self.headers])
        with self.assertRaises(ReadError):
            self.accept(body=b' ' * (MAX_BODY+1))
        with self.assertRaises(ReadError):
            self.accept(body=self.body+b' ')
        with self.assertRaises(ReadError):
            self.accept(body=None)  # explicit missing is not a request body

    def test_closed_ascii_json_rejects_duplicates_extra_keys_floats_and_nonfinite(self):
        for value in (
            self.body.replace(b'"actor":"repo:fixture"',b'"actor":"repo:fixture","actor":"bad"',1),
            self.body.replace(b'"actor":"repo:fixture"',b'"actor":"repo:fixture","extra":"bad"',1),
            self.body.replace(b'"issuedAt":1794877200',b'"issuedAt":1794877200.5',1),
            self.body.replace(b'"issuedAt":1794877200',b'"issuedAt":NaN',1),
            self.body.replace(b'"repoId":"fortis-approvals"', '"repoId":"é"'.encode('utf8'),1),
        ):
            with self.subTest(raw=value[:25]),self.assertRaises(ReadError):
                headers=[(k,str(len(value)) if k=='Content-Length' else v)
                         for k,v in self.headers]
                self.accept(body=value,headers=headers)

    def test_payload_cannot_choose_route_repo_actor_or_unsigned_claims(self):
        for key,val in (('repoId','other'),('requestId','bad'),('actor','new actor'),
                        ('configGeneration','f' * 64),('expectedHead','a'*40)):
            changed=candidate()
            changed[key]=val
            raw=serialize(changed)
            headers=[(k,str(len(raw)) if k=='Content-Length' else v)
                     for k,v in self.headers]
            with self.subTest(key=key),self.assertRaises(ReadError):
                self.accept(body=raw,headers=headers)
        changed=candidate()
        changed['proof']['claims']['actor']='different'
        raw=serialize(changed)
        headers=[(k,str(len(raw)) if k=='Content-Length' else v)
                 for k,v in self.headers]
        with self.assertRaises(ReadError):
            self.accept(body=raw,headers=headers)

    def test_exact_optional_idempotency_header_is_only_a_consistency_check(self):
        self.assertEqual(self.accept(headers=self.headers+[('Idempotency-Key',RID)]).request_id,RID)
        with self.assertRaises(ReadError):
            self.accept(headers=self.headers+[('Idempotency-Key','other')])
        with self.assertRaises(ReadError):
            self.accept(headers=self.headers+[
                ('Idempotency-Key',RID),('X-Idempotency-Key',RID)])
