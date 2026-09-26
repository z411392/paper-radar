import hashlib
import json
import re

from libs.retrieval.dtos.search_document import (
    PreparedSearchDocument,
    SearchDocumentInput,
    SearchProjectionDocument,
    SearchProjectionRow,
)
from libs.retrieval.exceptions.search_document_error import SearchDocumentError


class SearchDocumentRules:
    _WORK = re.compile(r"work:[0-9a-f]{64}")
    _REVISION = re.compile(r"revision:[0-9a-f]{64}")
    _KIND = re.compile(r"[a-z][a-z0-9_-]{0,63}")
    _LIMITS = {
        "title": 16384,
        "abstract": 1_000_000,
        "explanation": 1_000_000,
    }
    MAX_CONTENT_BYTES = 2_100_000

    @classmethod
    def _text(
        cls,
        value: object,
        field: str,
        *,
        require_nonblank: bool = False,
    ) -> str:
        if not isinstance(value, str) or "\0" in value:
            raise SearchDocumentError("invalid_search_document")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise SearchDocumentError("invalid_search_document") from None
        if len(encoded) > cls._LIMITS[field]:
            raise SearchDocumentError("invalid_search_document")
        if require_nonblank and not value.strip():
            raise SearchDocumentError("invalid_search_document")
        return value

    @classmethod
    def prepare(cls, value: SearchDocumentInput) -> PreparedSearchDocument:
        if (
            not isinstance(value, SearchDocumentInput)
            or not isinstance(value.work_id, str)
            or cls._WORK.fullmatch(value.work_id) is None
            or not isinstance(value.revision_id, str)
            or cls._REVISION.fullmatch(value.revision_id) is None
            or not isinstance(value.projection_kind, str)
            or cls._KIND.fullmatch(value.projection_kind) is None
        ):
            raise SearchDocumentError("invalid_search_document")
        title = cls._text(value.title, "title", require_nonblank=True)
        abstract = cls._text(value.abstract, "abstract")
        explanation = cls._text(value.explanation, "explanation")
        payload = {
            "format_version": 1,
            "work_id": value.work_id,
            "revision_id": value.revision_id,
            "projection_kind": value.projection_kind,
            "title": title,
            "abstract": abstract,
            "explanation": explanation,
        }
        try:
            content = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise SearchDocumentError("invalid_search_document") from exc
        if not 1 <= len(content) <= cls.MAX_CONTENT_BYTES:
            raise SearchDocumentError("invalid_search_document")
        fingerprint = hashlib.sha256(content).hexdigest()
        return PreparedSearchDocument(
            "searchdoc:" + fingerprint,
            value.work_id,
            value.revision_id,
            value.projection_kind,
            title,
            abstract,
            explanation,
            fingerprint,
            "extracted:" + fingerprint,
            content,
        )

    @classmethod
    def validate_prepared(cls, value: PreparedSearchDocument) -> None:
        if not isinstance(value, PreparedSearchDocument):
            raise SearchDocumentError("invalid_search_document")
        rebuilt = cls.prepare(
            SearchDocumentInput(
                value.work_id,
                value.revision_id,
                value.projection_kind,
                value.title,
                value.abstract,
                value.explanation,
            )
        )
        if rebuilt != value:
            raise SearchDocumentError("invalid_search_document")


    @classmethod
    def decode_projection(
        cls,
        content: bytes,
        document: SearchProjectionDocument,
    ) -> SearchProjectionRow:
        if (
            not isinstance(content, bytes)
            or not 1 <= len(content) <= cls.MAX_CONTENT_BYTES
            or not isinstance(document, SearchProjectionDocument)
        ):
            raise SearchDocumentError("search_document_projection_corrupt")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            payload = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("nonfinite")
                ),
            )
            if (
                not isinstance(payload, dict)
                or set(payload) != {
                    "format_version",
                    "work_id",
                    "revision_id",
                    "projection_kind",
                    "title",
                    "abstract",
                    "explanation",
                }
                or payload["format_version"] != 1
            ):
                raise ValueError("shape")
            rebuilt = cls.prepare(
                SearchDocumentInput(
                    payload["work_id"],
                    payload["revision_id"],
                    payload["projection_kind"],
                    payload["title"],
                    payload["abstract"],
                    payload["explanation"],
                )
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
            SearchDocumentError,
        ) as exc:
            raise SearchDocumentError("search_document_projection_corrupt") from exc

        if (
            rebuilt.content_bytes != content
            or rebuilt.document_id != document.document_id
            or rebuilt.work_id != document.work_id
            or rebuilt.revision_id != document.revision_id
            or rebuilt.projection_kind != document.projection_kind
            or rebuilt.text_object_id != document.text_object_id
            or rebuilt.input_fingerprint != document.input_fingerprint
        ):
            raise SearchDocumentError("search_document_projection_corrupt")
        return SearchProjectionRow(
            document.document_id,
            rebuilt.title,
            rebuilt.abstract,
            rebuilt.explanation,
        )
