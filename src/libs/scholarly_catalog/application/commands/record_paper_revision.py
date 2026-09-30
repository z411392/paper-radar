import hashlib
import json
from datetime import datetime

from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError
from libs.scholarly_catalog.ports.research_event_store_port import (
    ResearchEventStorePort,
)


class RecordPaperRevision:
    _KINDS = frozenset(
        {
            "new_work",
            "late_discovery",
            "revision_available",
            "metadata_changed",
            "publication_status_changed",
            "correction",
            "retraction",
        }
    )

    def __init__(self, store: ResearchEventStorePort) -> None:
        self._store = store

    @staticmethod
    def _text(value: object, maximum: int) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise PaperIdentityError("invalid_research_event")
        try:
            if len(value.encode("utf-8")) > maximum:
                raise PaperIdentityError("invalid_research_event")
        except UnicodeEncodeError:
            raise PaperIdentityError("invalid_research_event") from None
        return value

    @staticmethod
    def _hash(*parts: str) -> str:
        digest = hashlib.sha256()
        for part in parts:
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def __call__(
        self,
        *,
        work_id: str,
        revision_id: str | None,
        event_kind: str,
        source_evidence_id: str,
        source_evidence: dict[str, object],
        observed_at: datetime,
        occurred_at: datetime | None = None,
    ) -> str:
        work = self._text(work_id, 256)
        if revision_id is not None:
            revision = self._text(revision_id, 256)
        else:
            revision = None
        evidence_id = self._text(source_evidence_id, 512)
        if event_kind not in self._KINDS:
            raise PaperIdentityError("invalid_research_event")
        if (
            event_kind
            in {
                "new_work",
                "late_discovery",
                "revision_available",
                "metadata_changed",
            }
            and revision is None
        ):
            raise PaperIdentityError("research_event_revision_required")
        try:
            evidence_json = json.dumps(
                {
                    "format_version": 1,
                    "source_evidence_id": evidence_id,
                    "source_evidence": source_evidence,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError):
            raise PaperIdentityError("invalid_research_event") from None
        canonical_key = "research-event-key:" + self._hash(
            work,
            event_kind,
            evidence_id,
        )
        event_id = "research-event:" + self._hash(canonical_key)
        return self._store.register(
            event_id=event_id,
            work_id=work,
            revision_id=revision,
            event_kind=event_kind,
            canonical_event_key=canonical_key,
            source_evidence_json=evidence_json,
            occurred_at=occurred_at,
            observed_at=observed_at,
        )
