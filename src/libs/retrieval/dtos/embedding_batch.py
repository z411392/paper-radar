from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EmbeddingVectorEntry:
    document_id: str
    vector: tuple[float, ...]


@dataclass(frozen=True)
class EmbeddingBatchInput:
    space_id: str
    entries: tuple[EmbeddingVectorEntry, ...]


@dataclass(frozen=True)
class PreparedEmbeddingRow:
    document_id: str
    input_fingerprint: str
    row_offset: int


@dataclass(frozen=True)
class PreparedEmbeddingBatch:
    space_id: str
    space_configuration_fingerprint: str
    dimension: int
    object_id: str
    content_bytes: bytes = field(repr=False)
    rows: tuple[PreparedEmbeddingRow, ...] = ()


@dataclass(frozen=True)
class PersistedEmbedding:
    embedding_id: int
    document_id: str
    space_id: str
    object_id: str
    row_offset: int
    input_fingerprint: str
    created_at: datetime
    replayed: bool


@dataclass(frozen=True)
class PersistedEmbeddingBatch:
    object_id: str
    embeddings: tuple[PersistedEmbedding, ...]
