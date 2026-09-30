from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules as Rules
from libs.discovery.dtos.crossref_capture import CrossrefReplayedPage
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.ports.crossref_capture_store_port import CrossrefCaptureStorePort
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort


class ReplayCrossrefCapture:
    """Decode only a validated stored receipt; this command cannot contact a provider."""
    def __init__(self, store: CrossrefCaptureStorePort, source: CrossrefPageSourcePort) -> None:
        self._store, self._source = store, source

    def __call__(self, plan: CrossrefWindowPlan, request: CrossrefPageRequest,
                 receipt_id: str) -> CrossrefReplayedPage:
        stored = self._store.read(receipt_id)
        if stored.request != request:
            raise CrossrefCaptureError("crossref_capture_request_mismatch", receipt_id)
        entity = Rules.entity(stored.capture)
        if self._source.page(plan, request.cursor) != request:
            raise CrossrefCaptureError("crossref_capture_request_mismatch", receipt_id)
        digest = Rules.sha(entity)
        page = self._source.decode(plan, request, entity, expected_sha256=digest,
                                   http_status=200)  # entity() requires a complete HTTP 200 receipt.
        return CrossrefReplayedPage(receipt_id, stored.body_sha256, digest, page)
