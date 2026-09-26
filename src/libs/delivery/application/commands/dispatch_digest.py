from datetime import datetime

from libs.delivery.dtos.delivery_dispatch import (
    DispatchOutcome,
    MailMessage,
    MailSendResult,
)
from libs.delivery.ports.delivery_dispatch_store_port import DeliveryDispatchStorePort
from libs.delivery.ports.digest_artifact_reader_port import DigestArtifactReaderPort
from libs.delivery.ports.mail_sender_port import MailSenderPort
from libs.delivery.ports.recipient_resolver_port import RecipientResolverPort


class DispatchDigest:
    def __init__(
        self,
        *,
        store: DeliveryDispatchStorePort,
        artifacts: DigestArtifactReaderPort,
        recipients: RecipientResolverPort,
        sender: MailSenderPort,
    ) -> None:
        self._store = store
        self._artifacts = artifacts
        self._recipients = recipients
        self._sender = sender

    def __call__(self, outbox_id: str, *, now: datetime) -> DispatchOutcome:
        candidate = self._store.load_dispatch(outbox_id)
        if candidate.outbox_state != "pending":
            return DispatchOutcome(candidate.outbox_state, candidate.outbox_id)

        recipient = self._recipients.resolve(candidate.recipient_ref)
        if recipient is None or not isinstance(recipient, str) or not recipient.strip():
            return DispatchOutcome("recipient_missing", candidate.outbox_id)

        payload = self._artifacts.read(candidate.rendered_object_id)
        if (
            payload.subscription_id != candidate.subscription_id
            or payload.period_key != candidate.period_key
            or candidate.rendered_object_id != "digest:" + candidate.payload_sha256
        ):
            return DispatchOutcome("payload_mismatch", candidate.outbox_id)

        claim = self._store.claim_dispatch(
            candidate.outbox_id,
            now,
            expected_rendered_object_id=candidate.rendered_object_id,
            expected_payload_sha256=candidate.payload_sha256,
            expected_idempotency_key=candidate.idempotency_key,
        )
        if claim.state != "sending" or claim.attempt_id is None:
            return DispatchOutcome(claim.state, candidate.outbox_id, claim.attempt_id)

        message = MailMessage(
            recipient=recipient,
            subject=payload.subject,
            text_body=payload.text_body,
            html_body=payload.html_body,
            idempotency_key=candidate.idempotency_key,
        )
        try:
            result = self._sender.send(message)
        except Exception:
            result = MailSendResult("unknown", None, "mail_sender_exception")
        final_state = self._store.finish_dispatch(claim.attempt_id, result, now)
        return DispatchOutcome(final_state, candidate.outbox_id, claim.attempt_id)
