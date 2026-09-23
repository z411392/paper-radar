import hashlib
import json
import re
from datetime import datetime, timezone

from libs.scholarly_catalog.dtos.evidence_anchor import EvidenceAnchor
from libs.scholarly_catalog.dtos.evidence_anchor_request import EvidenceAnchorRequest
from libs.scholarly_catalog.dtos.evidence_object_receipt import EvidenceObjectReceipt
from libs.scholarly_catalog.dtos.evidence_preparation_input import EvidencePreparationInput
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import EvidenceSnapshotError


class EvidenceSnapshotRules:
    _LEVELS = {"abstract_only", "selected_sections", "full_text"}
    _SCOPES = {"abstract", "document"}

    @staticmethod
    def _json(value: object) -> str:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            encoded.encode("utf-8")
            return encoded
        except (TypeError, ValueError, RecursionError) as exc:
            raise EvidenceSnapshotError("invalid_evidence_json") from exc

    @staticmethod
    def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceSnapshotError("invalid_evidence_json")
            result[key] = value
        return result

    @staticmethod
    def _constant(_value: str) -> None:
        raise EvidenceSnapshotError("invalid_evidence_json")

    @classmethod
    def _decode_canonical_json(cls, value: str) -> object:
        if not isinstance(value, str):
            raise EvidenceSnapshotError("invalid_evidence_json")
        try:
            decoded = json.loads(
                value,
                object_pairs_hook=cls._unique,
                parse_constant=cls._constant,
            )
        except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
            raise EvidenceSnapshotError("invalid_evidence_json") from exc
        if cls._json(decoded) != value:
            raise EvidenceSnapshotError("invalid_evidence_json")
        return decoded

    @staticmethod
    def _identifier(value: object, *, code: str) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,127}", value) is None
        ):
            raise EvidenceSnapshotError(code)
        return value

    @staticmethod
    def _parser_version(value: object) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None
        ):
            raise EvidenceSnapshotError("invalid_parser_version")
        return value

    @staticmethod
    def _media_type(value: object) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or len(value) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise EvidenceSnapshotError("invalid_evidence_media_type")
        return value

    @staticmethod
    def _created_at(value: object) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise EvidenceSnapshotError("invalid_evidence_time")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (OverflowError, ValueError) as exc:
            raise EvidenceSnapshotError("invalid_evidence_time") from exc

    @staticmethod
    def _canonical_time(value: object) -> str:
        if not isinstance(value, str):
            raise EvidenceSnapshotError("invalid_evidence_time")
        try:
            moment = datetime.fromisoformat(value)
        except (ValueError, OverflowError) as exc:
            raise EvidenceSnapshotError("invalid_evidence_time") from exc
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise EvidenceSnapshotError("invalid_evidence_time")
        try:
            canonical = moment.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError) as exc:
            raise EvidenceSnapshotError("invalid_evidence_time") from exc
        if canonical != value:
            raise EvidenceSnapshotError("invalid_evidence_time")
        return canonical

    @staticmethod
    def _section(value: object) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _./:-]{0,127}", value) is None
        ):
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        return value

    @classmethod
    def _sections(cls, value: object, *, allow_empty: bool) -> tuple[str, ...]:
        if not isinstance(value, tuple):
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        items = tuple(cls._section(item) for item in value)
        if (
            (not allow_empty and not items)
            or len(items) != len(set(items))
            or items != tuple(sorted(items))
        ):
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        return items

    @staticmethod
    def _text(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or "\x00" in value or "\r" in value:
            raise EvidenceSnapshotError("invalid_evidence_text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise EvidenceSnapshotError("invalid_evidence_text") from exc
        return value

    @classmethod
    def _anchor_request(
        cls,
        value: EvidenceAnchorRequest,
        normalized_text: str | None,
        included_sections: tuple[str, ...],
    ) -> tuple[str, int, int, str | None, str | None, str | None]:
        if not isinstance(value, EvidenceAnchorRequest):
            raise EvidenceSnapshotError("invalid_evidence_anchor")
        quote = value.quote
        if (
            not isinstance(quote, str)
            or not quote
            or "\x00" in quote
            or type(value.offset_start) is not int
            or type(value.offset_end) is not int
            or value.offset_start < 0
            or value.offset_end <= value.offset_start
            or value.offset_end - value.offset_start != len(quote)
            or normalized_text is not None
            and (
                value.offset_end > len(normalized_text)
                or normalized_text[value.offset_start : value.offset_end] != quote
            )
        ):
            raise EvidenceSnapshotError("invalid_evidence_anchor")
        section = value.section_label
        if section is not None:
            section = cls._section(section)
            if section not in included_sections:
                raise EvidenceSnapshotError("invalid_evidence_anchor")
        paragraph = value.paragraph_id
        if paragraph is not None:
            if (
                not isinstance(paragraph, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", paragraph) is None
            ):
                raise EvidenceSnapshotError("invalid_evidence_anchor")
        table_json = value.table_locator_json
        if table_json is not None:
            decoded = cls._decode_canonical_json(table_json)
            if not isinstance(decoded, dict) or not decoded:
                raise EvidenceSnapshotError("invalid_evidence_anchor")
        return quote, value.offset_start, value.offset_end, section, paragraph, table_json

    @classmethod
    def _coverage_level(
        cls,
        scope: object,
        complete: object,
        included: tuple[str, ...],
        missing: tuple[str, ...],
    ) -> str:
        if not isinstance(scope, str) or scope not in cls._SCOPES or type(complete) is not bool:
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        if set(included) & set(missing):
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        if scope == "abstract":
            if complete or included != ("abstract",):
                raise EvidenceSnapshotError("invalid_evidence_coverage")
            return "abstract_only"
        if complete and missing:
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        return "full_text" if complete else "selected_sections"

    @classmethod
    def _validate_input(
        cls,
        value: EvidencePreparationInput,
    ) -> tuple[str, str, str, tuple[tuple[str, int, int, str | None, str | None, str | None], ...]]:
        if not isinstance(value, EvidencePreparationInput):
            raise EvidenceSnapshotError("invalid_evidence_input")
        cls._identifier(value.revision_id, code="invalid_revision_identity")
        cls._identifier(value.work_id, code="invalid_work_identity")
        if not isinstance(value.source_bytes, bytes) or not value.source_bytes:
            raise EvidenceSnapshotError("invalid_evidence_source")
        cls._media_type(value.source_media_type)
        cls._parser_version(value.parser_version)
        cls._created_at(value.created_at)

        if value.parser_error_code is not None:
            if not isinstance(value.parser_error_code, str) or not value.parser_error_code:
                raise EvidenceSnapshotError("invalid_parser_error")
            raise EvidenceSnapshotError("evidence_parse_failed")
        normalized_text = cls._text(value.normalized_text)
        included = cls._sections(value.included_sections, allow_empty=False)
        missing = cls._sections(value.missing_required_sections, allow_empty=True)
        level = cls._coverage_level(value.content_scope, value.document_complete, included, missing)

        if not isinstance(value.anchors, tuple) or not value.anchors:
            raise EvidenceSnapshotError("invalid_evidence_anchor")
        anchors = tuple(cls._anchor_request(item, normalized_text, included) for item in value.anchors)
        if len(anchors) != len(set(anchors)):
            raise EvidenceSnapshotError("invalid_evidence_anchor")

        coverage_json = cls._json(
            {
                "content_scope": value.content_scope,
                "document_complete": value.document_complete,
                "included_sections": list(included),
                "missing_required_sections": list(missing),
            }
        )
        return level, normalized_text, coverage_json, anchors

    @staticmethod
    def _receipt(
        receipt: EvidenceObjectReceipt,
        content: bytes,
        *,
        allowed_kinds: frozenset[str],
    ) -> None:
        if not isinstance(receipt, EvidenceObjectReceipt):
            raise EvidenceSnapshotError("invalid_evidence_object")
        digest = hashlib.sha256(content).hexdigest()
        if receipt.content_sha256 != digest:
            raise EvidenceSnapshotError("evidence_object_hash_mismatch")
        expected = {f"{kind}:{digest}" for kind in allowed_kinds}
        if not isinstance(receipt.object_id, str) or receipt.object_id not in expected:
            raise EvidenceSnapshotError("invalid_evidence_object")

    @classmethod
    def preview_level(cls, value: EvidencePreparationInput) -> str:
        level, _, _, _ = cls._validate_input(value)
        return level

    @classmethod
    def prepare(
        cls,
        value: EvidencePreparationInput,
        source: EvidenceObjectReceipt,
        text: EvidenceObjectReceipt,
    ) -> EvidenceSnapshot:
        level, normalized_text, coverage_json, anchor_requests = cls._validate_input(value)
        cls._receipt(
            source,
            value.source_bytes,
            allowed_kinds=frozenset({"evidence"} if level == "abstract_only" else {"fulltext"}),
        )
        cls._receipt(
            text,
            normalized_text.encode("utf-8"),
            allowed_kinds=frozenset({"extracted"}),
        )
        created_at = cls._created_at(value.created_at)
        fingerprint_input = cls._json(
            {
                "format_version": 1,
                "revision_id": value.revision_id,
                "work_id": value.work_id,
                "object_id": source.object_id,
                "text_object_id": text.object_id,
                "parser_version": value.parser_version,
                "evidence_level": level,
                "coverage": cls._decode_canonical_json(coverage_json),
            }
        )
        fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
        snapshot_id = f"snapshot:{fingerprint}"
        anchors = []
        for quote, start, end, section, paragraph, table_json in anchor_requests:
            payload = cls._json(
                {
                    "snapshot_id": snapshot_id,
                    "quote": quote,
                    "offset_start": start,
                    "offset_end": end,
                    "section_label": section,
                    "paragraph_id": paragraph,
                    "table_locator_json": table_json,
                }
            )
            anchor_id = "anchor:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
            anchors.append(
                EvidenceAnchor(
                    anchor_id,
                    snapshot_id,
                    section,
                    paragraph,
                    quote,
                    start,
                    end,
                    table_json,
                )
            )
        return EvidenceSnapshot(
            snapshot_id,
            value.revision_id,
            value.work_id,
            source.object_id,
            text.object_id,
            value.parser_version,
            level,
            coverage_json,
            fingerprint,
            created_at,
            tuple(anchors),
        )

    @classmethod
    def validate_metadata(cls, snapshot: EvidenceSnapshot) -> tuple[str, ...]:
        """Check deterministic metadata; verify() additionally checks the actual object bytes."""
        if not isinstance(snapshot, EvidenceSnapshot):
            raise EvidenceSnapshotError("invalid_evidence_snapshot")
        cls._identifier(snapshot.revision_id, code="invalid_revision_identity")
        cls._identifier(snapshot.work_id, code="invalid_work_identity")
        cls._parser_version(snapshot.parser_version)
        cls._canonical_time(snapshot.created_at)
        if not isinstance(snapshot.evidence_level, str) or snapshot.evidence_level not in cls._LEVELS:
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        coverage = cls._decode_canonical_json(snapshot.coverage_json)
        if not isinstance(coverage, dict) or set(coverage) != {
            "content_scope",
            "document_complete",
            "included_sections",
            "missing_required_sections",
        }:
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        included_raw = coverage["included_sections"]
        missing_raw = coverage["missing_required_sections"]
        if not isinstance(included_raw, list) or not isinstance(missing_raw, list):
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        included = cls._sections(tuple(included_raw), allow_empty=False)
        missing = cls._sections(tuple(missing_raw), allow_empty=True)
        level = cls._coverage_level(
            coverage["content_scope"], coverage["document_complete"], included, missing
        )
        if level != snapshot.evidence_level:
            raise EvidenceSnapshotError("invalid_evidence_coverage")
        source_kind = "evidence" if level == "abstract_only" else "fulltext"
        for object_id, kind in ((snapshot.object_id, source_kind), (snapshot.text_object_id, "extracted")):
            if not isinstance(object_id, str) or re.fullmatch(kind + r":[0-9a-f]{64}", object_id) is None:
                raise EvidenceSnapshotError("invalid_evidence_object")
        fingerprint_input = cls._json(
            {
                "format_version": 1,
                "revision_id": snapshot.revision_id,
                "work_id": snapshot.work_id,
                "object_id": snapshot.object_id,
                "text_object_id": snapshot.text_object_id,
                "parser_version": snapshot.parser_version,
                "evidence_level": snapshot.evidence_level,
                "coverage": coverage,
            }
        )
        fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
        if snapshot.fingerprint != fingerprint or snapshot.snapshot_id != f"snapshot:{fingerprint}":
            raise EvidenceSnapshotError("evidence_snapshot_fingerprint_mismatch")
        if not isinstance(snapshot.anchors, tuple) or not snapshot.anchors:
            raise EvidenceSnapshotError("invalid_evidence_anchor")
        seen: set[str] = set()
        for anchor in snapshot.anchors:
            if (
                not isinstance(anchor, EvidenceAnchor)
                or not isinstance(anchor.anchor_id, str)
                or anchor.snapshot_id != snapshot.snapshot_id
                or anchor.anchor_id in seen
            ):
                raise EvidenceSnapshotError("invalid_evidence_anchor")
            request = EvidenceAnchorRequest(
                anchor.quote,
                anchor.offset_start,
                anchor.offset_end,
                anchor.section_label,
                anchor.paragraph_id,
                anchor.table_locator_json,
            )
            quote, start, end, section, paragraph, table_json = cls._anchor_request(request, None, included)
            payload = cls._json(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "quote": quote,
                    "offset_start": start,
                    "offset_end": end,
                    "section_label": section,
                    "paragraph_id": paragraph,
                    "table_locator_json": table_json,
                }
            )
            expected_anchor = "anchor:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if anchor.anchor_id != expected_anchor:
                raise EvidenceSnapshotError("invalid_evidence_anchor")
            seen.add(anchor.anchor_id)
        return included

    @classmethod
    def verify(cls, snapshot: EvidenceSnapshot, source_bytes: bytes, normalized_text: str) -> None:
        included = cls.validate_metadata(snapshot)
        if not isinstance(source_bytes, bytes) or not source_bytes:
            raise EvidenceSnapshotError("invalid_evidence_source")
        text = cls._text(normalized_text)
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        source_kind = "evidence" if snapshot.evidence_level == "abstract_only" else "fulltext"
        if snapshot.object_id != f"{source_kind}:{source_hash}":
            raise EvidenceSnapshotError("evidence_object_hash_mismatch")
        if snapshot.text_object_id != f"extracted:{text_hash}":
            raise EvidenceSnapshotError("evidence_object_hash_mismatch")
        for anchor in snapshot.anchors:
            cls._anchor_request(
                EvidenceAnchorRequest(
                    anchor.quote,
                    anchor.offset_start,
                    anchor.offset_end,
                    anchor.section_label,
                    anchor.paragraph_id,
                    anchor.table_locator_json,
                ),
                text,
                included,
            )
