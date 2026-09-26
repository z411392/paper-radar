import hashlib
import os
import tempfile
from pathlib import Path

from libs.kernel.adapters.driven.workspace_paths import WorkspacePaths
from libs.kernel.exceptions.storage_error import StorageError
from libs.retrieval.domain.services.index_generation_rules import (
    IndexGenerationRules,
)
from libs.retrieval.dtos.index_build import PublishedIndexArtifacts
from libs.retrieval.dtos.index_generation import (
    PreparedIndexGeneration,
    PreparedIndexManifest,
)
from libs.retrieval.exceptions.active_index_error import ActiveIndexError
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.ports.active_index_artifact_repair_port import (
    ActiveIndexArtifactRepairPort,
)


class FilesystemActiveIndexArtifactRepairAdapter(
    ActiveIndexArtifactRepairPort
):
    _INDEX_NAME = "index.faiss"
    _MANIFEST_NAME = "manifest.json"

    def __init__(self, root: Path) -> None:
        self._paths = WorkspacePaths(root)

    def _replace(self, relative: str, content: bytes) -> None:
        try:
            target = self._paths.path(relative)
            parent_relative = target.parent.relative_to(
                self._paths.root
            ).as_posix()
            parent = self._paths.directory(parent_relative)
            fd, name = tempfile.mkstemp(
                prefix=".active-index-repair-",
                dir=parent,
            )
            staging = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(staging, target)
                self._paths.sync_directory(parent)
                verified = WorkspacePaths.read_regular(
                    target,
                    len(content) + 1,
                )
                if verified != content:
                    raise ActiveIndexError(
                        "active_index_repair_corrupt"
                    )
            finally:
                staging.unlink(missing_ok=True)
        except ActiveIndexError:
            raise
        except (OSError, StorageError) as exc:
            raise ActiveIndexError("active_index_repair_io") from exc

    def repair(
        self,
        generation: PreparedIndexGeneration,
        *,
        index_bytes: bytes,
        manifest: PreparedIndexManifest,
    ) -> PublishedIndexArtifacts:
        try:
            IndexGenerationRules.validate_prepared(generation)
            if not isinstance(index_bytes, bytes) or not index_bytes:
                raise IndexGenerationError("invalid_index_artifact")
            index_sha256 = hashlib.sha256(index_bytes).hexdigest()
            expected = IndexGenerationRules.manifest(
                generation,
                index_sha256=index_sha256,
            )
            if manifest != expected:
                raise IndexGenerationError(
                    "invalid_index_generation_manifest"
                )
        except IndexGenerationError as exc:
            raise ActiveIndexError(
                "active_index_repair_invalid"
            ) from exc
        if not self._paths.database().is_file():
            raise ActiveIndexError("workspace_missing")

        prefix = generation.relative_directory + "/"
        self._replace(prefix + self._INDEX_NAME, index_bytes)
        self._replace(prefix + self._MANIFEST_NAME, manifest.content_bytes)
        return PublishedIndexArtifacts(
            generation.generation_id,
            generation.relative_directory,
            index_sha256,
            manifest.manifest_sha256,
        )
