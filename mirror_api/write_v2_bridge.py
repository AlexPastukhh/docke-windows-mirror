"""P4 private source-only composer of the signed WRITE admission chain.

NO HTTP POST handler imports or constructs this bridge. The current TLS
Gateway v2 listener remains GET-only. The owner has not provisioned live
WRITE/signer credentials or enabled execution on Windows. This bridge is an
offline-tested internal prerequisite for a separately authorized release.

A service WRITE bearer validates transport *role*, NOT the actor or ASK.
The request body's actor is used only as a scoped *selector* before the
Ed25519 proof is verified by MachineReads.reserve_signed_candidate. Only a
fresh pinned signature and the actor-bound immutable persisted preview may
authorize a NEW reservation and the existing one-use worker handoff.
A matching historical UUID is readback-only and never starts Git again.
"""
from .machine_reads import GENERATION
from .read_v2 import CoreReadV2, require
from .write_v2_ingress import WriteIngressProfile


class PrivateWriteBridge:
    """Explicitly disabled by default; not a network endpoint or listener."""

    def __init__(self, *, ingress, reads, gateway_generation, source_enabled=False):
        require(type(ingress) is WriteIngressProfile and
                type(reads) is CoreReadV2 and
                type(gateway_generation) is str and
                GENERATION.fullmatch(gateway_generation), 'POLICY_DENIED')
        require(type(source_enabled) is bool, 'POLICY_DENIED')
        # The READ preview generation must be pinned to the same exact
        # owner-configured signing profile. Equal key bytes from a different
        # object are not authority; no caller-supplied pin override.
        require(reads._owner_signing_pins is ingress.signing_pins and
                ingress.allowed_repos <= reads.allowed_repos,
                'POLICY_DENIED')
        machine = reads._bound_reads()
        require(machine._signing_pins is ingress.signing_pins,
                'POLICY_DENIED')
        self._ingress = ingress
        self._machine = machine
        self._gateway_generation = gateway_generation
        self._enabled = source_enabled

    def __repr__(self):
        return '<PrivateWriteBridge source-only credentials=REDACTED>'

    def process_envelope(self, *, method, path, headers, body):
        """Internal composition only; no HTTP adapter or automatic activation.

        Future owner-reviewed network code MUST first establish actual pinned
        TLS peer/interface, reject oversized Content-Length before socket read,
        supply immutable configured generation and never log secrets/body.
        This does NOT establish actor from the body before signature checks.
        """
        require(self._enabled is True, 'POLICY_DENIED')
        candidate = self._ingress.accept(method, path, headers, body)
        dispatch = candidate.untrusted_dispatch()
        # Untrusted selector, not an authenticated principal until proof and
        # original actor-scoped persisted preview pass in MachineReads.
        actor_selector = dispatch['actor']
        return self._machine.dispatch_signed_candidate(
            dispatch=dispatch, actor=actor_selector,
            repo=candidate.repo_id, request_id=candidate.request_id,
            gateway_generation=self._gateway_generation,
            owner_pins=self._ingress.signing_pins)
