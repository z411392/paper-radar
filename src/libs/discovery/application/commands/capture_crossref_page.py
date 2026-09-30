from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules
from libs.discovery.dtos.crossref_capture import CrossrefCaptureResult
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan
from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.ports.crossref_capture_store_port import CrossrefCaptureStorePort
from libs.discovery.ports.crossref_http_transport_port import CrossrefHttpTransportPort
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort
from libs.discovery.ports.crossref_rate_gate_port import CrossrefRateGatePort


class CaptureCrossrefPage:
    """No decoder or checkpoint dependency: every returned response is saved first."""
    def __init__(self, transport: CrossrefHttpTransportPort, gate: CrossrefRateGatePort,
                 store: CrossrefCaptureStorePort, *, source: CrossrefPageSourcePort,
                 enabled: bool = False) -> None:
        if type(enabled) is not bool:
            raise CrossrefCaptureError("invalid_crossref_capture_configuration")
        self._transport, self._gate, self._store, self._source = transport, gate, store, source
        self._enabled = enabled

    def __call__(self, plan: CrossrefWindowPlan, request: CrossrefPageRequest,
                 *, attempt_key: str) -> CrossrefCaptureResult:
        if not self._enabled:
            raise CrossrefCaptureError("crossref_capture_disabled")
        CrossrefCaptureRules.request_shape(request)
        CrossrefCaptureRules.text(attempt_key, "invalid_crossref_capture_attempt")
        if self._source.page(plan, request.cursor) != request:
            raise CrossrefCaptureError("invalid_crossref_capture_request")
        receipt_id = None
        decision: CrossrefRateDecision | None = None
        try:
            with self._gate.slot(plan.definition.contact_email) as lease:
                capture = self._transport.get(plan, request)
                try:
                    receipt_id = self._store.save(request, capture, attempt_key=attempt_key)
                except Exception as storage_error:
                    # Even a disk failure must not bypass a provider block or retry delay.
                    try:
                        lease.observe(capture.status, capture.headers, capture_error=capture.capture_error)
                    except Exception:
                        storage_error.add_note("crossref_gate_observation_also_failed")
                    raise
                try:
                    decision = lease.observe(
                        capture.status, capture.headers, capture_error=capture.capture_error
                    )
                except Exception as exc:
                    raise CrossrefCaptureError("crossref_gate_observation_failed", receipt_id) from exc
        except CrossrefCaptureError:
            raise
        except Exception as exc:
            if receipt_id is not None:
                raise CrossrefCaptureError("crossref_gate_finalize_failed", receipt_id) from exc
            raise
        if receipt_id is None or decision is None:
            raise CrossrefCaptureError("crossref_capture_incomplete", receipt_id)
        return CrossrefCaptureResult(receipt_id, decision)
