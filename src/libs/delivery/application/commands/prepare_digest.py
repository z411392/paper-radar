import hashlib
import json

from libs.delivery.domain.services.digest_selection_rules import DigestSelectionRules
from libs.delivery.dtos.digest_preview import DigestPreview, PrepareDigestRequest
from libs.delivery.templates.digest_preview import DigestPreviewTemplate


class PrepareDigest:
    def __call__(self, request: PrepareDigestRequest) -> DigestPreview:
        cutoff, settings_url = DigestSelectionRules.validate_request(request)
        items = DigestSelectionRules.select(request)
        if not items:
            return DigestPreview(
                subscription_id=request.subscription_id,
                period_key=request.period_key,
                cutoff_at=cutoff,
                queueable=False,
                items=(),
                subject="",
                text_body="",
                html_body="",
                content_fingerprint=None,
            )

        subject, text_body, html_body = DigestPreviewTemplate.render(
            items,
            settings_url=settings_url,
            coverage_notes=request.coverage_notes,
        )
        identity = {
            "subscription_id": request.subscription_id,
            "period_key": request.period_key,
            "cutoff_at": cutoff.isoformat(),
            "items": [
                {
                    "event_id": item.event_id,
                    "work_id": item.work_id,
                    "summary_id": item.summary_id,
                    "revision_id": item.revision_id,
                    "item_kind": item.item_kind,
                    "event_kind": item.event_kind,
                    "domains": item.domains,
                }
                for item in items
            ],
            "subject": subject,
            "text_body": text_body,
            "html_body": html_body,
            "coverage_notes": request.coverage_notes,
        }
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return DigestPreview(
            subscription_id=request.subscription_id,
            period_key=request.period_key,
            cutoff_at=cutoff,
            queueable=True,
            items=items,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            content_fingerprint=hashlib.sha256(encoded).hexdigest(),
        )
