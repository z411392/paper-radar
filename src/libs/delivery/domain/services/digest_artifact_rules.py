import json

from libs.delivery.dtos.digest_artifact import StoredDigestPayload
from libs.delivery.dtos.digest_preview import DigestPreview


class DigestArtifactError(ValueError):
    pass


class DigestArtifactRules:
    VERSION = 2

    @staticmethod
    def serialize(preview: DigestPreview) -> bytes:
        if (
            not isinstance(preview, DigestPreview)
            or preview.queueable is not True
            or preview.content_fingerprint is None
            or not preview.items
        ):
            raise DigestArtifactError("digest_not_queueable")
        payload = {
            "schema_version": DigestArtifactRules.VERSION,
            "subscription_id": preview.subscription_id,
            "period_key": preview.period_key,
            "cutoff_at": preview.cutoff_at.isoformat(),
            "content_fingerprint": preview.content_fingerprint,
            "subject": preview.subject,
            "text_body": preview.text_body,
            "html_body": preview.html_body,
            "items": [
                {
                    "position": position,
                    "event_id": item.event_id,
                    "work_id": item.work_id,
                    "summary_id": item.summary_id,
                    "revision_id": item.revision_id,
                    "item_kind": item.item_kind,
                    "event_kind": item.event_kind,
                }
                for position, item in enumerate(preview.items, start=1)
            ],
        }
        try:
            return json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise DigestArtifactError("invalid_digest_artifact") from exc

    @staticmethod
    def parse(content: bytes) -> StoredDigestPayload:
        if not isinstance(content, bytes) or not content or len(content) > 4 * 1024 * 1024:
            raise DigestArtifactError("invalid_digest_artifact")
        try:
            data = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DigestArtifactError("invalid_digest_artifact") from exc
        required = {
            "schema_version",
            "subscription_id",
            "period_key",
            "cutoff_at",
            "content_fingerprint",
            "subject",
            "text_body",
            "html_body",
            "items",
        }
        if (
            not isinstance(data, dict)
            or set(data) != required
            or data["schema_version"] not in {1, 2}
        ):
            raise DigestArtifactError("invalid_digest_artifact")
        for key in (
            "subscription_id",
            "period_key",
            "content_fingerprint",
            "subject",
            "text_body",
            "html_body",
        ):
            if not isinstance(data[key], str) or not data[key]:
                raise DigestArtifactError("invalid_digest_artifact")
        if not isinstance(data["items"], list) or not data["items"]:
            raise DigestArtifactError("invalid_digest_artifact")
        if data["schema_version"] == 2:
            required_item = {
                "position",
                "event_id",
                "work_id",
                "summary_id",
                "revision_id",
                "item_kind",
                "event_kind",
            }
            for item in data["items"]:
                if not isinstance(item, dict) or set(item) != required_item:
                    raise DigestArtifactError("invalid_digest_artifact")
                if item["item_kind"] not in {"paper", "status_notice"}:
                    raise DigestArtifactError("invalid_digest_artifact")
        return StoredDigestPayload(
            subscription_id=data["subscription_id"],
            period_key=data["period_key"],
            content_fingerprint=data["content_fingerprint"],
            subject=data["subject"],
            text_body=data["text_body"],
            html_body=data["html_body"],
        )
