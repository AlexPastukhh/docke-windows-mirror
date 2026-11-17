"""Source-only, UNREGISTERED P4 Windows WRITE handler candidate.

The running Gateway v1/v2 factory keeps ReadOnlyHandler/ReadV2Handler and
continues to return 405 for every POST. This class is not registered, imported
by a factory/launcher, or reachable over an active network interface.

A future owner-reviewed factory must separately prove its selected Windows
TLS socket, exact approved source network namespace/peer, credential and key
ACLs, and independently activate a configured private bridge. Constructing
this class or committing its source is NOT permission to activate WRITE.
"""
from http import HTTPStatus
import re
import ssl

from .gateway_v2 import ReadV2Handler
from .read_v2 import ReadError
from .write_v2_bridge import PrivateWriteBridge
from .write_v2_ingress import MAX_BODY, MAX_META, PATH


class PrivateWriteV2Handler(ReadV2Handler):
    """Opt-in source candidate that reuses the *existing* HTTPS server Core.

    Neither the shipping Gateway v2 factory nor any CLI/launcher uses this
    class. No separate listener, port, second Core, or Git executor is built.
    """
    def _transport_allowed(self):
        # An HTTPS Host header does not prove a TLS socket or a trusted peer.
        # A future protected factory must provide an independently reviewed
        # peer gate. The callback must return exact True, not a truthy object.
        try:
            if not isinstance(self.connection, ssl.SSLSocket):
                return False
            if self.connection.version() not in ('TLSv1.2', 'TLSv1.3'):
                return False
            gate = getattr(self.server, 'owner_write_peer_gate', None)
            return callable(gate) and gate(self.client_address) is True
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def do_POST(self):
        # Do not keep a pipelined connection alive after accepting/refusing a
        # request whose body might be malformed or incompletely consumed.
        self.close_connection = True
        if (getattr(self.server, 'owner_write_opt_in', None) is not True or
                type(getattr(self.server, 'private_write_bridge', None)) is not PrivateWriteBridge or
                self.server.private_write_bridge._enabled is not True):
            self.deny_write()
            return
        try:
            if not self._transport_allowed():
                self.send_json(HTTPStatus.FORBIDDEN, {'error': 'GATEWAY_WRITE_TRANSPORT_DENIED'})
                return
            if (type(self.path) is not str or len(self.path) > MAX_META or
                    PATH.fullmatch(self.path) is None):
                self.send_json(HTTPStatus.BAD_REQUEST, {'error': 'GATEWAY_WRITE_ROUTE_DENIED'})
                return
            raw = list(self.headers.raw_items())
            if (len(raw) > 16 or sum(len(k) + len(v) + 4 for k, v in raw)
                    + len(self.path) > MAX_META or
                    any(k.lower() in ('transfer-encoding', 'expect', 'content-encoding')
                        for k, _ in raw)):
                self.send_json(HTTPStatus.BAD_REQUEST, {'error': 'GATEWAY_WRITE_FRAME_DENIED'})
                return
            lengths = [v for k, v in raw if k.lower() == 'content-length']
            # Check length BEFORE allocating or reading anything from rfile.
            if (len(lengths) != 1 or
                    re.fullmatch(r'[1-9][0-9]{0,3}', lengths[0]) is None or
                    not 0 < int(lengths[0]) <= MAX_BODY):
                self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                               {'error': 'GATEWAY_WRITE_LENGTH_DENIED'})
                return
            length = int(lengths[0])
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_json(HTTPStatus.BAD_REQUEST, {'error': 'GATEWAY_WRITE_TRUNCATED'})
                return
            # The private bridge validates the remaining raw auth/Host/body
            # ambiguities, verifies pinned ASK proof and checks durable UUID
            # /fresh preconditions before any effect.
            result = self.server.private_write_bridge.process_envelope(
                method='POST', path=self.path, headers=raw, body=body)
            self.send_json(HTTPStatus.OK, result)
        except ReadError:
            # No exception string, bearer, HTTP path, actor or payload in
            # responses/logs. The client may query the original UUID through
            # its principal-bound READ result endpoint after ambiguity.
            self.send_json(HTTPStatus.FORBIDDEN, {'error': 'GATEWAY_WRITE_DENIED'})
        except Exception:
            # Effect may have started before the response failed. Never assert
            # that Git did nothing, never trigger a retry here.
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE,
                           {'error': 'OUTCOME_UNCONFIRMED'})
