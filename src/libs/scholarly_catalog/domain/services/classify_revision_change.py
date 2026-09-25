import re
from dataclasses import dataclass


class RevisionChangeError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RevisionChangeClassification:
    event_kinds: tuple[str, ...]
    content_changed: bool
    metadata_changed: bool
    publication_status_changed: bool
    requires_regeneration: bool


class ClassifyRevisionChange:
    _STATUSES = frozenset(
        {
            "preprint",
            "published",
            "corrected",
            "retracted",
            "withdrawn",
            "unknown",
        }
    )
    _INTEGRITY = frozenset({"correction", "retraction"})

    @staticmethod
    def _fingerprint(value: object, *, nullable: bool = False) -> str | None:
        if value is None and nullable:
            return None
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise RevisionChangeError("invalid_revision_fingerprint")
        return value

    @classmethod
    def _status(cls, value: object, *, nullable: bool = False) -> str | None:
        if value is None and nullable:
            return None
        if not isinstance(value, str) or value not in cls._STATUSES:
            raise RevisionChangeError("invalid_publication_status")
        return value

    def __call__(
        self,
        *,
        previous_content_fingerprint: str | None,
        current_content_fingerprint: str,
        previous_metadata_fingerprint: str | None,
        current_metadata_fingerprint: str,
        previous_publication_status: str | None,
        current_publication_status: str,
        integrity_event_kind: str | None = None,
    ) -> RevisionChangeClassification:
        previous_content = self._fingerprint(
            previous_content_fingerprint,
            nullable=True,
        )
        current_content = self._fingerprint(current_content_fingerprint)
        previous_metadata = self._fingerprint(
            previous_metadata_fingerprint,
            nullable=True,
        )
        current_metadata = self._fingerprint(current_metadata_fingerprint)
        previous_status = self._status(
            previous_publication_status,
            nullable=True,
        )
        current_status = self._status(current_publication_status)
        if (
            integrity_event_kind is not None
            and integrity_event_kind not in self._INTEGRITY
        ):
            raise RevisionChangeError("unsupported_integrity_event_kind")

        content_changed = (
            previous_content is not None
            and previous_content != current_content
        )
        metadata_changed = (
            previous_metadata is not None
            and previous_metadata != current_metadata
        )
        publication_status_changed = (
            previous_status is not None
            and previous_status != current_status
        )

        events: list[str] = []
        if content_changed:
            events.append("revision_available")
        elif metadata_changed:
            events.append("metadata_changed")
        if publication_status_changed:
            events.append("publication_status_changed")
        if integrity_event_kind is not None:
            events.append(integrity_event_kind)

        return RevisionChangeClassification(
            tuple(events),
            content_changed,
            metadata_changed,
            publication_status_changed,
            content_changed,
        )
