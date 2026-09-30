"""Replay publication from the durable inbox, never from provider HTTP.

No claim completion, gate decision or journal attachment occurs here. Any caller that
wants those actions must independently validate the current claim and shared gate.
"""
from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules as Rules
from libs.discovery.dtos.crossref_capture import CrossrefStoredCapture
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.exceptions.crossref_capture_inbox_error import CrossrefCaptureInboxError as Error
from libs.discovery.ports.crossref_capture_inbox_port import CrossrefCaptureInboxPort
from libs.discovery.ports.crossref_capture_store_port import CrossrefCaptureStorePort


class PublishClaimedCrossrefCapture:
    def __init__(self, inbox: CrossrefCaptureInboxPort, captures: CrossrefCaptureStorePort) -> None:
        self._inbox = inbox
        self._captures = captures

    def __call__(self, claim: CrossrefCaptureClaim) -> CrossrefStoredCapture:
        record = self._inbox.load(claim)
        if record is None:
            raise Error('crossref_inbox_response_missing')
        try:
            receipt_id = self._captures.save(record.request, record.capture, attempt_key=claim.claim_id)
            stored = self._captures.read(receipt_id)
            if (not isinstance(stored, CrossrefStoredCapture) or stored.receipt_id != receipt_id
                    or stored.attempt_key != claim.claim_id or stored.request != record.request
                    or stored.capture != record.capture or stored.body_sha256 != record.body_sha256
                    or stored.body_object_id != 'raw:'+record.body_sha256):
                raise Error('crossref_inbox_publication_mismatch')
            Rules.validate(stored.capture)
            Rules.object_id(receipt_id)
        except Error:
            raise
        except Exception as exc:
            raise Error('crossref_inbox_publication_failed') from exc
        # Capture publication is local I/O, outside SQLite transactions. A restored
        # workspace observed after publication must not receive a current-looking result.
        if self._inbox.load(claim) != record:
            raise Error('crossref_inbox_changed')
        return stored
