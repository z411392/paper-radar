import hashlib
import json
import re

from libs.retrieval.dtos.index_generation import (
    IndexGenerationInput,
    IndexGenerationMember,
    IndexGenerationSnapshot,
    PreparedIndexGeneration,
    PreparedIndexManifest,
)
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError


class IndexGenerationRules:
    _HASH = re.compile(r"[0-9a-f]{64}")
    _SPACE = re.compile(r"embspace:[0-9a-f]{64}")
    _DOCUMENT = re.compile(r"searchdoc:[0-9a-f]{64}")
    _OBJECT = re.compile(r"embedding:[0-9a-f]{64}")
    _KIND = "flat-idmap-v1"

    @classmethod
    def _text(cls, value: object, *, maximum_bytes: int = 128) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or "\0" in value
        ):
            raise IndexGenerationError("invalid_index_generation")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise IndexGenerationError("invalid_index_generation") from None
        if size > maximum_bytes:
            raise IndexGenerationError("invalid_index_generation")
        return value

    @staticmethod
    def _canonical(value: object) -> bytes:
        try:
            return json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise IndexGenerationError("invalid_index_generation") from exc

    @classmethod
    def _member_payload(cls, member: IndexGenerationMember) -> dict[str, object]:
        return {
            "embedding_id": member.embedding_id,
            "document_id": member.document_id,
            "object_id": member.object_id,
            "row_offset": member.row_offset,
            "input_fingerprint": member.input_fingerprint,
            "document_sequence_no": member.document_sequence_no,
        }

    @classmethod
    def validate_snapshot(cls, value: IndexGenerationSnapshot) -> None:
        if (
            not isinstance(value, IndexGenerationSnapshot)
            or cls._SPACE.fullmatch(value.space_id) is None
            or cls._HASH.fullmatch(value.space_configuration_fingerprint) is None
            or type(value.dimension) is not int
            or value.dimension < 1
            or value.dtype != "float32"
            or value.metric not in {"inner_product", "l2"}
            or type(value.document_high_watermark) is not int
            or value.document_high_watermark < 1
            or type(value.vector_count) is not int
            or value.vector_count < 1
            or not isinstance(value.members, tuple)
            or len(value.members) != value.vector_count
        ):
            raise IndexGenerationError("invalid_index_generation_snapshot")

        embedding_ids = []
        document_ids = []
        object_rows = []
        sequences = []
        for member in value.members:
            if (
                not isinstance(member, IndexGenerationMember)
                or type(member.embedding_id) is not int
                or member.embedding_id < 1
                or cls._DOCUMENT.fullmatch(member.document_id) is None
                or cls._OBJECT.fullmatch(member.object_id) is None
                or type(member.row_offset) is not int
                or member.row_offset < 0
                or cls._HASH.fullmatch(member.input_fingerprint) is None
                or type(member.document_sequence_no) is not int
                or member.document_sequence_no < 1
            ):
                raise IndexGenerationError("invalid_index_generation_snapshot")
            embedding_ids.append(member.embedding_id)
            document_ids.append(member.document_id)
            object_rows.append((member.object_id, member.row_offset))
            sequences.append(member.document_sequence_no)

        if (
            embedding_ids != sorted(embedding_ids)
            or len(set(embedding_ids)) != len(embedding_ids)
            or len(set(document_ids)) != len(document_ids)
            or len(set(object_rows)) != len(object_rows)
            or max(sequences) != value.document_high_watermark
        ):
            raise IndexGenerationError("invalid_index_generation_snapshot")

    @classmethod
    def prepare(
        cls,
        snapshot: IndexGenerationSnapshot,
        value: IndexGenerationInput,
    ) -> PreparedIndexGeneration:
        cls.validate_snapshot(snapshot)
        if (
            not isinstance(value, IndexGenerationInput)
            or value.space_id != snapshot.space_id
            or value.index_kind != cls._KIND
        ):
            raise IndexGenerationError("invalid_index_generation")
        builder_version = cls._text(value.builder_version)
        faiss_version = cls._text(value.faiss_version)

        membership_content = cls._canonical(
            {
                "format_version": 1,
                "space_id": snapshot.space_id,
                "members": [
                    cls._member_payload(member)
                    for member in snapshot.members
                ],
            }
        )
        membership_digest = hashlib.sha256(membership_content).hexdigest()
        identity = {
            "format_version": 1,
            "space_id": snapshot.space_id,
            "space_configuration_fingerprint":
                snapshot.space_configuration_fingerprint,
            "dimension": snapshot.dimension,
            "dtype": snapshot.dtype,
            "metric": snapshot.metric,
            "index_kind": value.index_kind,
            "builder_version": builder_version,
            "faiss_version": faiss_version,
            "document_high_watermark": snapshot.document_high_watermark,
            "vector_count": snapshot.vector_count,
            "membership_digest": membership_digest,
        }
        generation_digest = hashlib.sha256(
            cls._canonical(identity)
        ).hexdigest()
        return PreparedIndexGeneration(
            "faissgen:" + generation_digest,
            snapshot.space_id,
            snapshot.space_configuration_fingerprint,
            snapshot.dimension,
            snapshot.dtype,
            snapshot.metric,
            value.index_kind,
            builder_version,
            faiss_version,
            snapshot.document_high_watermark,
            snapshot.vector_count,
            membership_digest,
            "derived/faiss/"
            + snapshot.space_id.removeprefix("embspace:")
            + "/"
            + generation_digest,
            snapshot.members,
        )

    @classmethod
    def validate_prepared(cls, value: PreparedIndexGeneration) -> None:
        if not isinstance(value, PreparedIndexGeneration):
            raise IndexGenerationError("invalid_index_generation")
        rebuilt = cls.prepare(
            IndexGenerationSnapshot(
                value.space_id,
                value.space_configuration_fingerprint,
                value.dimension,
                value.dtype,
                value.metric,
                value.document_high_watermark,
                value.vector_count,
                value.members,
            ),
            IndexGenerationInput(
                value.space_id,
                value.index_kind,
                value.builder_version,
                value.faiss_version,
            ),
        )
        if rebuilt != value:
            raise IndexGenerationError("invalid_index_generation")

    @classmethod
    def manifest(
        cls,
        generation: PreparedIndexGeneration,
        *,
        index_sha256: str,
    ) -> PreparedIndexManifest:
        cls.validate_prepared(generation)
        if (
            not isinstance(index_sha256, str)
            or cls._HASH.fullmatch(index_sha256) is None
        ):
            raise IndexGenerationError("invalid_index_generation_manifest")
        payload = {
            "format_version": 1,
            "generation_id": generation.generation_id,
            "space_id": generation.space_id,
            "space_configuration_fingerprint":
                generation.space_configuration_fingerprint,
            "dimension": generation.dimension,
            "dtype": generation.dtype,
            "metric": generation.metric,
            "index_kind": generation.index_kind,
            "builder_version": generation.builder_version,
            "faiss_version": generation.faiss_version,
            "document_high_watermark":
                generation.document_high_watermark,
            "vector_count": generation.vector_count,
            "membership_digest": generation.membership_digest,
            "index_sha256": index_sha256,
            "members": [
                cls._member_payload(member)
                for member in generation.members
            ],
        }
        content = cls._canonical(payload)
        return PreparedIndexManifest(
            generation.generation_id,
            content,
            hashlib.sha256(content).hexdigest(),
        )
