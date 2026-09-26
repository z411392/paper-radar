from dataclasses import dataclass, field


@dataclass(frozen=True)
class IndexBuildVector:
    embedding_id: int
    values: tuple[float, ...] = field(repr=False)


@dataclass(frozen=True)
class IndexBuildRequest:
    generation_id: str
    dimension: int
    metric: str
    vectors: tuple[IndexBuildVector, ...] = field(repr=False)


@dataclass(frozen=True)
class BuiltIndex:
    generation_id: str
    vector_count: int
    content_bytes: bytes = field(repr=False)


@dataclass(frozen=True)
class PublishedIndexArtifacts:
    generation_id: str
    relative_directory: str
    index_sha256: str
    manifest_sha256: str
