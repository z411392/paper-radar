from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class IndexGenerationMember:
    embedding_id: int
    document_id: str
    object_id: str
    row_offset: int
    input_fingerprint: str
    document_sequence_no: int


@dataclass(frozen=True)
class IndexGenerationSnapshot:
    space_id: str
    space_configuration_fingerprint: str
    dimension: int
    dtype: str
    metric: str
    document_high_watermark: int
    vector_count: int
    members: tuple[IndexGenerationMember, ...]


@dataclass(frozen=True)
class IndexGenerationInput:
    space_id: str
    index_kind: str
    builder_version: str
    faiss_version: str


@dataclass(frozen=True)
class PreparedIndexGeneration:
    generation_id: str
    space_id: str
    space_configuration_fingerprint: str
    dimension: int
    dtype: str
    metric: str
    index_kind: str
    builder_version: str
    faiss_version: str
    document_high_watermark: int
    vector_count: int
    membership_digest: str
    relative_directory: str
    members: tuple[IndexGenerationMember, ...]


@dataclass(frozen=True)
class PreparedIndexManifest:
    generation_id: str
    content_bytes: bytes = field(repr=False)
    manifest_sha256: str = ""


@dataclass(frozen=True)
class PersistedIndexGeneration:
    generation_id: str
    space_id: str
    state: str
    relative_directory: str
    membership_digest: str
    document_high_watermark: int
    vector_count: int
    created_at: datetime
    replayed: bool
