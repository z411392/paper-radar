"""Versioned, bounded claim/response envelope. No database, filesystem or network access."""
from dataclasses import asdict
from datetime import datetime, timezone

from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules as CaptureRules
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_capture_inbox import CrossrefCaptureInboxRecord
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_capture_inbox_error import CrossrefCaptureInboxError as Error

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CrossrefCaptureInboxRules:
    @staticmethod
    def instant_us(value: datetime) -> int:
        try:
            delta = CaptureRules.instant(value) - EPOCH
            return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        except CrossrefCaptureError as exc:
            raise Error('invalid_crossref_inbox_time') from exc

    @classmethod
    def identity(cls, claim: CrossrefCaptureClaim) -> dict:
        if (not isinstance(claim, CrossrefCaptureClaim)
                or type(claim.fencing_token) is not int or not 1 <= claim.fencing_token < 2**63
                or type(claim.workspace_epoch) is not int or not 1 <= claim.workspace_epoch < 2**63
                or not isinstance(claim.state, str) or claim.state not in {'reserved', 'dispatching'}):
            raise Error('invalid_crossref_inbox_claim')
        try:
            for value in (claim.claim_id, claim.page_id, claim.pass_id, claim.owner_id, claim.workspace_id):
                CaptureRules.text(value, 'invalid_crossref_inbox_claim', 512)
            CaptureRules.request_shape(claim.request)
        except CrossrefCaptureError as exc:
            raise Error('invalid_crossref_inbox_claim') from exc
        reserved = cls.instant_us(claim.reserved_at)
        until = cls.instant_us(claim.lease_until)
        if until <= reserved:
            raise Error('invalid_crossref_inbox_claim')
        # DTO state is only a caller snapshot; S1 begin_dispatch returns no updated DTO.
        # Authorization always checks the database state, not this snapshot.
        return dict(id=claim.claim_id, page_id=claim.page_id, pass_id=claim.pass_id,
                    fencing_token=claim.fencing_token, owner_id=claim.owner_id,
                    workspace_id=claim.workspace_id, workspace_epoch=claim.workspace_epoch,
                    request_json=CaptureRules.canonical(asdict(claim.request)).decode('ascii'),
                    reserved_us=reserved, lease_until_us=until)

    @classmethod
    def encode(cls, claim: CrossrefCaptureClaim, capture: CrossrefHttpCapture) -> bytes:
        identity = cls.identity(claim)
        try:
            CaptureRules.validate(capture)
            content = CaptureRules.canonical(dict(
                schema_version=1, claim=identity, status=capture.status, headers=capture.headers,
                received_at=CaptureRules.instant(capture.received_at).isoformat(),
                complete=capture.complete, capture_error=capture.capture_error,
                body_sha256=CaptureRules.sha(capture.body), body_size=len(capture.body),
            ))
            if len(content) > CaptureRules.MAX_RECEIPT:
                raise Error('crossref_inbox_envelope_too_large')
            return content
        except (CrossrefCaptureError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise Error('invalid_crossref_inbox_capture') from exc

    @classmethod
    def decode(cls, claim: CrossrefCaptureClaim, envelope: bytes, envelope_sha: str,
               body: bytes, body_sha: str) -> CrossrefCaptureInboxRecord:
        try:
            if (not isinstance(body, bytes) or len(body) > CaptureRules.MAX_BODY
                    or not isinstance(envelope, bytes) or len(envelope) > CaptureRules.MAX_RECEIPT
                    or CaptureRules.sha(envelope) != envelope_sha or CaptureRules.sha(body) != body_sha):
                raise ValueError('content hash or size')
            data = CaptureRules.load_receipt(envelope)
            if (set(data) != {'schema_version','claim','status','headers','received_at','complete',
                             'capture_error','body_sha256','body_size'}
                    or type(data['schema_version']) is not int or data['schema_version'] != 1
                    or data['claim'] != cls.identity(claim)
                    or type(data['body_size']) is not int or data['body_size'] != len(body)
                    or data['body_sha256'] != body_sha
                    or not isinstance(data['headers'], list)
                    or any(not isinstance(p, list) or len(p) != 2 for p in data['headers'])):
                raise ValueError('envelope shape or identity')
            capture = CrossrefHttpCapture(data['status'], tuple(tuple(p) for p in data['headers']), body,
                datetime.fromisoformat(data['received_at']), data['complete'], data['capture_error'])
            if cls.encode(claim, capture) != envelope:
                raise ValueError('noncanonical envelope')
        except (Error, CrossrefCaptureError, ValueError, TypeError, KeyError, UnicodeError,
                OverflowError, RecursionError) as exc:
            raise Error('crossref_inbox_corrupt') from exc
        return CrossrefCaptureInboxRecord(claim.claim_id, claim.request, capture, envelope_sha, body_sha)
