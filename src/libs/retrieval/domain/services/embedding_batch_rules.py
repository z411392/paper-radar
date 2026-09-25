import hashlib
import json
import re
from datetime import datetime, timezone

from libs.retrieval.domain.services.embedding_space_rules import EmbeddingSpaceRules
from libs.retrieval.domain.services.npy_float32_batch_codec import (
    NpyFloat32BatchCodec,
)
from libs.retrieval.dtos.embedding_batch import (
    EmbeddingBatchInput,
    PreparedEmbeddingBatch,
    PreparedEmbeddingRow,
)
from libs.retrieval.dtos.embedding_space import (
    EmbeddingSpaceInput,
    RegisteredEmbeddingSpace,
)
from libs.retrieval.exceptions.embedding_batch_error import EmbeddingBatchError
from libs.retrieval.exceptions.embedding_space_error import EmbeddingSpaceError


class EmbeddingBatchRules:
    _DOCUMENT = re.compile(r"searchdoc:[0-9a-f]{64}")
    _SPACE = re.compile(r"embspace:[0-9a-f]{64}")
    MAX_BATCH_SIZE = 10_000

    @classmethod
    def _validate_space(cls, space: RegisteredEmbeddingSpace) -> None:
        if (
            not isinstance(space, RegisteredEmbeddingSpace)
            or cls._SPACE.fullmatch(space.space_id) is None
        ):
            raise EmbeddingBatchError("invalid_embedding_space")
        try:
            prepared = EmbeddingSpaceRules.prepare(
                EmbeddingSpaceInput(
                    space.provider,
                    space.model_name,
                    space.model_revision,
                    space.dimension,
                    space.dtype,
                    space.normalization_version,
                    space.prefix_config_hash,
                    space.metric,
                )
            )
        except EmbeddingSpaceError as exc:
            raise EmbeddingBatchError("invalid_embedding_space") from exc
        if (
            prepared.space_id != space.space_id
            or prepared.configuration_fingerprint
            != space.configuration_fingerprint
        ):
            raise EmbeddingBatchError("invalid_embedding_space")

    @staticmethod
    def _input_fingerprint(document_id: str, space_id: str) -> str:
        content = json.dumps(
            {
                "format_version": 1,
                "document_id": document_id,
                "space_id": space_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return hashlib.sha256(content).hexdigest()

    @classmethod
    def prepare(
        cls,
        space: RegisteredEmbeddingSpace,
        value: EmbeddingBatchInput,
    ) -> PreparedEmbeddingBatch:
        cls._validate_space(space)
        if (
            not isinstance(value, EmbeddingBatchInput)
            or value.space_id != space.space_id
            or not isinstance(value.entries, tuple)
            or not 1 <= len(value.entries) <= cls.MAX_BATCH_SIZE
        ):
            raise EmbeddingBatchError("invalid_embedding_batch")

        ordered = sorted(value.entries, key=lambda item: item.document_id)
        if any(
            not hasattr(entry, "document_id")
            or not isinstance(entry.document_id, str)
            or cls._DOCUMENT.fullmatch(entry.document_id) is None
            or not isinstance(entry.vector, tuple)
            for entry in ordered
        ):
            raise EmbeddingBatchError("invalid_embedding_batch")
        document_ids = tuple(entry.document_id for entry in ordered)
        if len(set(document_ids)) != len(document_ids):
            raise EmbeddingBatchError("embedding_document_duplicate")

        vectors = tuple(entry.vector for entry in ordered)
        content = NpyFloat32BatchCodec.encode(
            vectors,
            dimension=space.dimension,
        )
        digest = hashlib.sha256(content).hexdigest()
        rows = tuple(
            PreparedEmbeddingRow(
                entry.document_id,
                cls._input_fingerprint(entry.document_id, space.space_id),
                offset,
            )
            for offset, entry in enumerate(ordered)
        )
        return PreparedEmbeddingBatch(
            space.space_id,
            space.configuration_fingerprint,
            space.dimension,
            "embedding:" + digest,
            content,
            rows,
        )

    @classmethod
    def validate_prepared(cls, value: PreparedEmbeddingBatch) -> None:
        if not isinstance(value, PreparedEmbeddingBatch):
            raise EmbeddingBatchError("invalid_embedding_batch")
        if (
            cls._SPACE.fullmatch(value.space_id) is None
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                value.space_configuration_fingerprint,
            )
            or type(value.dimension) is not int
            or value.dimension < 1
            or not isinstance(value.content_bytes, bytes)
            or not value.object_id.startswith("embedding:")
            or value.object_id[10:]
            != hashlib.sha256(value.content_bytes).hexdigest()
            or not isinstance(value.rows, tuple)
            or not value.rows
        ):
            raise EmbeddingBatchError("invalid_embedding_batch")
        decoded = NpyFloat32BatchCodec.decode(value.content_bytes)
        if len(decoded) != len(value.rows):
            raise EmbeddingBatchError("invalid_embedding_batch")
        for expected_offset, row in enumerate(value.rows):
            if (
                not isinstance(row, PreparedEmbeddingRow)
                or cls._DOCUMENT.fullmatch(row.document_id) is None
                or not re.fullmatch(r"[0-9a-f]{64}", row.input_fingerprint)
                or row.row_offset != expected_offset
                or row.input_fingerprint
                != cls._input_fingerprint(row.document_id, value.space_id)
            ):
                raise EmbeddingBatchError("invalid_embedding_batch")

    @staticmethod
    def instant(value: object) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise EmbeddingBatchError("embedding_batch_time")
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise EmbeddingBatchError("embedding_batch_time") from None
