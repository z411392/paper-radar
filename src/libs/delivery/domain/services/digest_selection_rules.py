from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlsplit

from libs.delivery.dtos.digest_preview import (
    DigestCandidate,
    PrepareDigestRequest,
    SelectedDigestItem,
)


class DigestSelectionError(ValueError):
    pass


class DigestSelectionRules:
    @staticmethod
    def _text(value: object, code: str, *, maximum: int = 4096) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
            raise DigestSelectionError(code)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise DigestSelectionError(code) from None
        return value

    @staticmethod
    def _instant(value: object, code: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise DigestSelectionError(code)
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise DigestSelectionError(code) from None

    @classmethod
    def _url(cls, value: object, code: str) -> str | None:
        if value is None:
            return None
        text = cls._text(value, code, maximum=2048)
        try:
            parsed = urlsplit(text)
            host = parsed.hostname
        except ValueError:
            raise DigestSelectionError(code) from None
        if parsed.scheme == "https":
            if not host or parsed.username is not None or parsed.password is not None:
                raise DigestSelectionError(code)
            return text
        if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
            if parsed.username is not None or parsed.password is not None:
                raise DigestSelectionError(code)
            return text
        raise DigestSelectionError(code)

    @classmethod
    def validate_request(cls, request: PrepareDigestRequest) -> tuple[datetime, str | None]:
        if not isinstance(request, PrepareDigestRequest):
            raise DigestSelectionError("invalid_digest_request")
        cls._text(request.subscription_id, "invalid_subscription_id", maximum=256)
        cls._text(request.period_key, "invalid_period_key", maximum=128)
        cutoff = cls._instant(request.cutoff_at, "invalid_cutoff")
        if type(request.max_items) is not int or request.max_items < 1 or request.max_items > 100:
            raise DigestSelectionError("invalid_max_items")
        if not isinstance(request.candidates, tuple) or len(request.candidates) > 10000:
            raise DigestSelectionError("invalid_digest_candidates")
        settings_url = cls._url(request.settings_url, "invalid_settings_url")
        if not isinstance(request.coverage_notes, tuple) or len(request.coverage_notes) > 128:
            raise DigestSelectionError("invalid_coverage_notes")
        for note in request.coverage_notes:
            cls._text(note, "invalid_coverage_notes", maximum=1024)
        return cutoff, settings_url

    @classmethod
    def _candidate(cls, value: object) -> DigestCandidate:
        if not isinstance(value, DigestCandidate):
            raise DigestSelectionError("invalid_digest_candidate")
        for name, item in (("event", value.event_id), ("work", value.work_id)):
            cls._text(item, f"invalid_{name}_id", maximum=256)
        if value.item_kind not in {"paper", "status_notice"}:
            raise DigestSelectionError("invalid_item_kind")
        cls._text(value.event_kind, "invalid_event_kind", maximum=64)

        if value.item_kind == "paper":
            for name, item in (
                ("summary", value.summary_id),
                ("revision", value.revision_id),
                ("current_summary", value.current_summary_id),
                ("current_revision", value.current_revision_id),
            ):
                cls._text(item, f"invalid_{name}_id", maximum=256)
            if value.qa_state not in {"pending", "passed", "rejected"}:
                raise DigestSelectionError("invalid_qa_state")
            if value.event_kind not in {
                "new_work",
                "late_discovery",
                "revision_available",
                "newly_accessible",
            }:
                raise DigestSelectionError("invalid_event_kind")
        else:
            if any(
                item is not None
                for item in (
                    value.summary_id,
                    value.revision_id,
                    value.current_summary_id,
                    value.current_revision_id,
                )
            ):
                raise DigestSelectionError("invalid_status_notice_reference")
            if value.qa_state != "not_applicable":
                raise DigestSelectionError("invalid_qa_state")
            if value.event_kind not in {"correction", "retraction"}:
                raise DigestSelectionError("invalid_event_kind")

        event_at = cls._instant(value.event_at, "invalid_event_at")
        if type(value.priority) is not int or not -1_000_000 <= value.priority <= 1_000_000:
            raise DigestSelectionError("invalid_priority")
        if not isinstance(value.domains, tuple) or len(value.domains) > 64:
            raise DigestSelectionError("invalid_domains")
        if value.item_kind == "paper" and not value.domains:
            raise DigestSelectionError("invalid_domains")
        domains = tuple(
            sorted(
                {
                    cls._text(item, "invalid_domain", maximum=128)
                    for item in value.domains
                }
            )
        )
        cls._text(value.title, "invalid_title", maximum=2048)
        source_url = cls._url(value.source_url, "invalid_source_url")
        if (
            not isinstance(value.plain_language, tuple)
            or not value.plain_language
            or len(value.plain_language) > 64
        ):
            raise DigestSelectionError("invalid_plain_language")
        for line in value.plain_language:
            cls._text(line, "invalid_plain_language", maximum=8192)
        return replace(value, event_at=event_at, domains=domains, source_url=source_url)

    @staticmethod
    def _eligible(candidate: DigestCandidate, cutoff: datetime) -> bool:
        if candidate.event_at > cutoff:
            return False
        if candidate.item_kind == "status_notice":
            return True
        return (
            candidate.qa_state == "passed"
            and candidate.summary_id == candidate.current_summary_id
            and candidate.revision_id == candidate.current_revision_id
        )

    @staticmethod
    def _merge(existing: SelectedDigestItem, candidate: DigestCandidate) -> SelectedDigestItem:
        same = (
            existing.event_id == candidate.event_id
            and existing.summary_id == candidate.summary_id
            and existing.revision_id == candidate.revision_id
            and existing.event_at == candidate.event_at
            and existing.priority == candidate.priority
            and existing.title == candidate.title
            and existing.source_url == candidate.source_url
            and existing.plain_language == candidate.plain_language
            and existing.item_kind == candidate.item_kind
            and existing.event_kind == candidate.event_kind
        )
        if not same:
            raise DigestSelectionError("conflicting_digest_candidates")
        domains = tuple(sorted(set(existing.domains) | set(candidate.domains)))
        return replace(existing, domains=domains)

    @classmethod
    def select(cls, request: PrepareDigestRequest) -> tuple[SelectedDigestItem, ...]:
        cutoff, _ = cls.validate_request(request)
        selected: dict[tuple[str, str], SelectedDigestItem] = {}
        for raw in request.candidates:
            candidate = cls._candidate(raw)
            if not cls._eligible(candidate, cutoff):
                continue
            item = SelectedDigestItem(
                event_id=candidate.event_id,
                work_id=candidate.work_id,
                summary_id=candidate.summary_id,
                revision_id=candidate.revision_id,
                event_at=candidate.event_at,
                priority=candidate.priority,
                domains=candidate.domains,
                title=candidate.title,
                source_url=candidate.source_url,
                plain_language=candidate.plain_language,
                item_kind=candidate.item_kind,
                event_kind=candidate.event_kind,
            )
            identity = (
                ("paper", candidate.work_id)
                if candidate.item_kind == "paper"
                else ("status_notice", candidate.event_id)
            )
            existing = selected.get(identity)
            selected[identity] = item if existing is None else cls._merge(existing, candidate)

        items = list(selected.values())
        items.sort(key=lambda item: item.event_id)
        items.sort(key=lambda item: item.event_at, reverse=True)
        items.sort(key=lambda item: item.priority, reverse=True)
        return tuple(items[: request.max_items])
