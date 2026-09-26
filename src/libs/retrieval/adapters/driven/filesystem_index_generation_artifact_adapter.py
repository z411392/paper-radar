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
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.ports.index_generation_artifact_store_port import (
    IndexGenerationArtifactStorePort,
)


class FilesystemIndexGenerationArtifactAdapter(
    IndexGenerationArtifactStorePort
):
    _INDEX_NAME = "index.faiss"
    _MANIFEST_NAME = "manifest.json"

    def __init__(self, root: Path) -> None:
        self._paths = WorkspacePaths(root)

    def _write_exact(self, relative: str, content: bytes) -> None:
        try:
            target = self._paths.path(relative)
            parent_relative = target.parent.relative_to(
                self._paths.root
            ).as_posix()
            parent = self._paths.directory(parent_relative)
            if target.exists():
                existing = WorkspacePaths.read_regular(
                    target,
                    len(content) + 1,
                )
                if existing != content:
                    raise IndexGenerationError(
                        "index_artifact_conflict"
                    )
                return
            temporary = self._paths.directory("tmp")
            fd, name = tempfile.mkstemp(
                prefix=".index-generation-",
                dir=temporary,
            )
            staging = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(staging, target)
                except FileExistsError:
                    existing = WorkspacePaths.read_regular(
                        target,
                        len(content) + 1,
                    )
                    if existing != content:
                        raise IndexGenerationError(
                            "index_artifact_conflict"
                        )
                self._paths.sync_directory(parent)
                verified = WorkspacePaths.read_regular(
                    target,
                    len(content) + 1,
                )
                if verified != content:
                    raise IndexGenerationError(
                        "index_artifact_corrupt"
                    )
            finally:
                staging.unlink(missing_ok=True)
        except IndexGenerationError:
            raise
        except (OSError, StorageError) as exc:
            raise IndexGenerationError("index_artifact_io") from exc

    def publish(
        self,
        generation: PreparedIndexGeneration,
        *,
        index_bytes: bytes,
        manifest: PreparedIndexManifest,
    ) -> PublishedIndexArtifacts:
        IndexGenerationRules.validate_prepared(generation)
        if not isinstance(index_bytes, bytes) or not index_bytes:
            raise IndexGenerationError("invalid_index_artifact")
        index_sha256 = hashlib.sha256(index_bytes).hexdigest()
        expected_manifest = IndexGenerationRules.manifest(
            generation,
            index_sha256=index_sha256,
        )
        if manifest != expected_manifest:
            raise IndexGenerationError("invalid_index_generation_manifest")
        if not self._paths.database().is_file():
            raise IndexGenerationError("workspace_missing")

        self._write_exact(
            generation.relative_directory + "/" + self._INDEX_NAME,
            index_bytes,
        )
        self._write_exact(
            generation.relative_directory + "/" + self._MANIFEST_NAME,
            manifest.content_bytes,
        )
        return PublishedIndexArtifacts(
            generation.generation_id,
            generation.relative_directory,
            index_sha256,
            manifest.manifest_sha256,
        )
