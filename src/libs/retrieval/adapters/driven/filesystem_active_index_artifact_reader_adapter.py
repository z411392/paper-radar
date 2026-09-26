import hashlib
import json
from pathlib import Path

from libs.kernel.adapters.driven.workspace_paths import WorkspacePaths
from libs.kernel.exceptions.storage_error import StorageError
from libs.retrieval.domain.services.index_generation_rules import (
    IndexGenerationRules,
)
from libs.retrieval.dtos.active_index import ActiveIndexPin
from libs.retrieval.dtos.active_index_artifact import ActiveIndexArtifacts
from libs.retrieval.dtos.index_generation import (
    IndexGenerationInput,
    IndexGenerationMember,
    IndexGenerationSnapshot,
)
from libs.retrieval.exceptions.active_index_error import ActiveIndexError
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.ports.active_index_artifact_reader_port import (
    ActiveIndexArtifactReaderPort,
)


class FilesystemActiveIndexArtifactReaderAdapter(
    ActiveIndexArtifactReaderPort
):
    _INDEX_NAME = "index.faiss"
    _MANIFEST_NAME = "manifest.json"
    _MAX_INDEX_BYTES = 512_000_000
    _MAX_MANIFEST_BYTES = 128_000_000

    def __init__(self, root: Path) -> None:
        self._paths = WorkspacePaths(root)

    @staticmethod
    def _sha(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _load_manifest(content: bytes) -> dict[str, object]:
        if not isinstance(content, bytes) or not content:
            raise ActiveIndexError("active_index_manifest_mismatch")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            value = json.loads(
                content.decode("ascii"),
                object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("nonfinite")
                ),
            )
        except (
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            RecursionError,
        ):
            raise ActiveIndexError(
                "active_index_manifest_mismatch"
            ) from None
        if not isinstance(value, dict):
            raise ActiveIndexError("active_index_manifest_mismatch")
        return value

    @staticmethod
    def _member(value: object) -> IndexGenerationMember:
        if not isinstance(value, dict) or set(value) != {
            "embedding_id",
            "document_id",
            "object_id",
            "row_offset",
            "input_fingerprint",
            "document_sequence_no",
        }:
            raise ActiveIndexError("active_index_manifest_mismatch")
        try:
            return IndexGenerationMember(
                value["embedding_id"],
                value["document_id"],
                value["object_id"],
                value["row_offset"],
                value["input_fingerprint"],
                value["document_sequence_no"],
            )
        except TypeError:
            raise ActiveIndexError(
                "active_index_manifest_mismatch"
            ) from None

    @classmethod
    def _prepared(cls, pin: ActiveIndexPin, data: dict[str, object]):
        expected_keys = {
            "format_version",
            "generation_id",
            "space_id",
            "space_configuration_fingerprint",
            "dimension",
            "dtype",
            "metric",
            "index_kind",
            "builder_version",
            "faiss_version",
            "document_high_watermark",
            "vector_count",
            "membership_digest",
            "index_sha256",
            "members",
        }
        if set(data) != expected_keys or data.get("format_version") != 1:
            raise ActiveIndexError("active_index_manifest_mismatch")
        members_value = data.get("members")
        if not isinstance(members_value, list):
            raise ActiveIndexError("active_index_manifest_mismatch")
        members = tuple(cls._member(item) for item in members_value)
        try:
            snapshot = IndexGenerationSnapshot(
                data["space_id"],
                data["space_configuration_fingerprint"],
                data["dimension"],
                data["dtype"],
                data["metric"],
                data["document_high_watermark"],
                data["vector_count"],
                members,
            )
            prepared = IndexGenerationRules.prepare(
                snapshot,
                IndexGenerationInput(
                    data["space_id"],
                    data["index_kind"],
                    data["builder_version"],
                    data["faiss_version"],
                ),
            )
        except (IndexGenerationError, TypeError):
            raise ActiveIndexError(
                "active_index_manifest_mismatch"
            ) from None

        if (
            data["generation_id"] != prepared.generation_id
            or data["membership_digest"] != prepared.membership_digest
            or data["index_sha256"] != pin.index_sha256
            or prepared.generation_id != pin.generation_id
            or prepared.space_id != pin.space_id
            or prepared.relative_directory != pin.relative_directory
            or prepared.dimension != pin.dimension
            or prepared.dtype != pin.dtype
            or prepared.metric != pin.metric
            or prepared.document_high_watermark
            != pin.document_high_watermark
            or prepared.vector_count != pin.vector_count
            or prepared.membership_digest != pin.membership_digest
        ):
            raise ActiveIndexError("active_index_manifest_mismatch")
        return prepared

    def _read(self, relative: str, limit: int) -> bytes:
        try:
            target = self._paths.path(relative)
            if not target.exists():
                raise ActiveIndexError("active_index_artifact_missing")
            content = WorkspacePaths.read_regular(target, limit + 1)
        except ActiveIndexError:
            raise
        except FileNotFoundError:
            raise ActiveIndexError(
                "active_index_artifact_missing"
            ) from None
        except (OSError, StorageError) as exc:
            raise ActiveIndexError("active_index_artifact_corrupt") from exc
        if len(content) > limit:
            raise ActiveIndexError("active_index_artifact_corrupt")
        return content

    def read(self, pin: ActiveIndexPin) -> ActiveIndexArtifacts:
        if not isinstance(pin, ActiveIndexPin):
            raise ActiveIndexError("invalid_active_index")

        prefix = pin.relative_directory + "/"
        index_bytes = self._read(
            prefix + self._INDEX_NAME,
            self._MAX_INDEX_BYTES,
        )
        manifest_bytes = self._read(
            prefix + self._MANIFEST_NAME,
            self._MAX_MANIFEST_BYTES,
        )
        if self._sha(index_bytes) != pin.index_sha256:
            raise ActiveIndexError("active_index_artifact_corrupt")
        if self._sha(manifest_bytes) != pin.manifest_sha256:
            raise ActiveIndexError("active_index_artifact_corrupt")

        data = self._load_manifest(manifest_bytes)
        prepared = self._prepared(pin, data)
        try:
            expected = IndexGenerationRules.manifest(
                prepared,
                index_sha256=pin.index_sha256,
            )
        except IndexGenerationError as exc:
            raise ActiveIndexError(
                "active_index_manifest_mismatch"
            ) from exc
        if (
            expected.content_bytes != manifest_bytes
            or expected.manifest_sha256 != pin.manifest_sha256
        ):
            raise ActiveIndexError("active_index_manifest_mismatch")

        return ActiveIndexArtifacts(
            pin,
            index_bytes,
            manifest_bytes,
            tuple(member.embedding_id for member in prepared.members),
        )
