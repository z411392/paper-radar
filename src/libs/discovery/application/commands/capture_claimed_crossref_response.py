"""Acquire provider budget, cross the send boundary, then durably stage the response."""
from collections.abc import Callable
from datetime import datetime, timezone

from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_claimed_capture import CrossrefClaimedCapture
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.ports.claimed_crossref_attachment_port import (
    PublishClaimedCrossrefCapturePort,
)
from libs.discovery.ports.crossref_capture_claim_store_port import (
    CrossrefCaptureClaimStorePort,
)
from libs.discovery.ports.crossref_capture_inbox_port import CrossrefCaptureInboxPort
from libs.discovery.ports.crossref_http_transport_port import CrossrefHttpTransportPort
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort
from libs.discovery.ports.crossref_rate_gate_port import CrossrefRateGatePort


class CaptureClaimedCrossrefResponse:
    def __init__(
        self,
        transport: CrossrefHttpTransportPort,
        gate: CrossrefRateGatePort,
        claims: CrossrefCaptureClaimStorePort,
        inbox: CrossrefCaptureInboxPort,
        publish: PublishClaimedCrossrefCapturePort,
        *,
        source: CrossrefPageSourcePort,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._transport = transport
        self._gate = gate
        self._claims = claims
        self._inbox = inbox
        self._publish = publish
        self._source = source
        self._clock = clock

    def __call__(
        self,
        plan: CrossrefWindowPlan,
        claim: CrossrefCaptureClaim,
    ) -> CrossrefClaimedCapture:
        try:
            if (
                not isinstance(claim, CrossrefCaptureClaim)
                or self._source.page(plan, claim.request.cursor) != claim.request
            ):
                raise CrossrefCaptureError("crossref_claimed_capture_request_mismatch")
        except CrossrefProtocolError as exc:
            raise CrossrefCaptureError(
                "crossref_claimed_capture_request_mismatch"
            ) from exc

        with self._gate.slot(plan.definition.contact_email) as lease:
            # This is the one-shot send boundary. If slot acquisition failed,
            # the claim stays reserved and no HTTP attempt is recorded.
            self._claims.begin_dispatch(claim, now=self._clock())
            capture = self._transport.get(plan, claim.request)
            try:
                self._inbox.stage(
                    claim,
                    capture,
                    staged_at=self._clock(),
                )
            except Exception as storage_error:
                # A received response must still tighten provider state even
                # when local staging fails.
                try:
                    lease.observe(
                        capture.status,
                        capture.headers,
                        capture_error=capture.capture_error,
                    )
                except Exception:
                    storage_error.add_note(
                        "crossref_gate_observation_also_failed"
                    )
                raise
            decision = lease.observe(
                capture.status,
                capture.headers,
                capture_error=capture.capture_error,
            )
        stored = self._publish(claim)
        return CrossrefClaimedCapture(stored, decision)
