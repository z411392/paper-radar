from datetime import datetime

from libs.scholarly_catalog.application.commands.record_paper_revision import (
    RecordPaperRevision,
)
from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityAssertionRef,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError
from libs.scholarly_catalog.ports.read_crossref_integrity_event_source_port import (
    ReadCrossrefIntegrityEventSourcePort,
)


class PromoteCrossrefIntegrityEvents:
    def __init__(
        self,
        read_source: ReadCrossrefIntegrityEventSourcePort,
        record: RecordPaperRevision,
    ) -> None:
        self._read_source = read_source
        self._record = record

    @staticmethod
    def _occurred_at(
        value: str | None,
        precision: str | None,
    ) -> datetime | None:
        if precision != "second":
            return None
        if value is None:
            raise CrossrefProviderProjectionError(
                "crossref_integrity_event_source_corrupt"
            )
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise CrossrefProviderProjectionError(
                "crossref_integrity_event_source_corrupt"
            ) from None
        if result.tzinfo is None or result.utcoffset() is None:
            raise CrossrefProviderProjectionError(
                "crossref_integrity_event_source_corrupt"
            )
        return result

    def __call__(
        self,
        assertions: tuple[CrossrefIntegrityAssertionRef, ...],
    ) -> tuple[str, ...]:
        if not isinstance(assertions, tuple):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_event_promotion"
            )
        events: list[str] = []
        for assertion in assertions:
            if not isinstance(assertion, CrossrefIntegrityAssertionRef):
                raise CrossrefProviderProjectionError(
                    "invalid_crossref_integrity_event_promotion"
                )
            source = self._read_source(assertion.assertion_id)
            if source is None:
                continue
            if source.event_class not in {"correction", "retraction"}:
                continue
            evidence = {
                "provider": "crossref",
                "assertion_id": source.assertion_id,
                "wire_direction": source.wire_direction,
                "notice_canonical_doi": source.notice_canonical_doi,
                "target_canonical_doi": source.target_canonical_doi,
                "type_raw": source.type_raw,
                "source_raw": source.source_raw,
                "label_raw": source.label_raw,
                "record_id_raw_json": source.record_id_raw_json,
                "updated_value": source.updated_value,
                "updated_precision": source.updated_precision,
                "updated_raw_json": source.updated_raw_json,
                "raw_json": source.raw_json,
                "provider_attributed": True,
            }
            try:
                event_id = self._record(
                    work_id=source.canonical_work_id,
                    revision_id=None,
                    event_kind=source.event_class,
                    source_evidence_id=source.assertion_id,
                    source_evidence=evidence,
                    observed_at=source.first_observed_at,
                    occurred_at=self._occurred_at(
                        source.updated_value,
                        source.updated_precision,
                    ),
                )
            except PaperIdentityError as exc:
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_event_write_failed"
                ) from exc
            events.append(event_id)
        return tuple(events)
