import hashlib
import json
import re
from datetime import datetime, timezone

from libs.retrieval.dtos.embedding_space import (
    EmbeddingSpaceInput,
    PreparedEmbeddingSpace,
)
from libs.retrieval.exceptions.embedding_space_error import EmbeddingSpaceError


class EmbeddingSpaceRules:
    _HASH = re.compile(r"[0-9a-f]{64}")
    _METRICS = frozenset({"inner_product", "l2"})

    @staticmethod
    def _text(value: object, *, maximum_bytes: int = 512) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or "\0" in value
        ):
            raise EmbeddingSpaceError("invalid_embedding_space")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise EmbeddingSpaceError("invalid_embedding_space") from None
        if size > maximum_bytes:
            raise EmbeddingSpaceError("invalid_embedding_space")
        return value

    @classmethod
    def prepare(cls, value: EmbeddingSpaceInput) -> PreparedEmbeddingSpace:
        if not isinstance(value, EmbeddingSpaceInput):
            raise EmbeddingSpaceError("invalid_embedding_space")
        provider = cls._text(value.provider)
        model_name = cls._text(value.model_name)
        model_revision = cls._text(value.model_revision)
        normalization = cls._text(value.normalization_version)
        if (
            type(value.dimension) is not int
            or not 1 <= value.dimension <= 1_000_000
            or value.dtype != "float32"
            or value.metric not in cls._METRICS
            or not isinstance(value.prefix_config_hash, str)
            or cls._HASH.fullmatch(value.prefix_config_hash) is None
        ):
            raise EmbeddingSpaceError("invalid_embedding_space")
        payload = {
            "format_version": 1,
            "provider": provider,
            "model_name": model_name,
            "model_revision": model_revision,
            "dimension": value.dimension,
            "dtype": value.dtype,
            "normalization_version": normalization,
            "prefix_config_hash": value.prefix_config_hash,
            "metric": value.metric,
        }
        try:
            content = json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise EmbeddingSpaceError("invalid_embedding_space") from exc
        fingerprint = hashlib.sha256(content).hexdigest()
        return PreparedEmbeddingSpace(
            "embspace:" + fingerprint,
            provider,
            model_name,
            model_revision,
            value.dimension,
            value.dtype,
            normalization,
            value.prefix_config_hash,
            value.metric,
            fingerprint,
        )

    @classmethod
    def validate_prepared(cls, value: PreparedEmbeddingSpace) -> None:
        if not isinstance(value, PreparedEmbeddingSpace):
            raise EmbeddingSpaceError("invalid_embedding_space")
        rebuilt = cls.prepare(
            EmbeddingSpaceInput(
                value.provider,
                value.model_name,
                value.model_revision,
                value.dimension,
                value.dtype,
                value.normalization_version,
                value.prefix_config_hash,
                value.metric,
            )
        )
        if rebuilt != value:
            raise EmbeddingSpaceError("invalid_embedding_space")

    @staticmethod
    def instant(value: object) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise EmbeddingSpaceError("embedding_space_time")
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise EmbeddingSpaceError("embedding_space_time") from None
