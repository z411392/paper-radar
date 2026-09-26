from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchDocumentInput:
    work_id: str
    revision_id: str
    projection_kind: str
    title: str
    abstract: str
    explanation: str


@dataclass(frozen=True)
class PreparedSearchDocument:
    document_id: str
    work_id: str
    revision_id: str
    projection_kind: str
    title: str
    abstract: str
    explanation: str
    input_fingerprint: str
    text_object_id: str
    content_bytes: bytes = field(repr=False)


@dataclass(frozen=True)
class PersistedSearchDocument:
    document_id: str
    work_id: str
    revision_id: str
    projection_kind: str
    text_object_id: str
    input_fingerprint: str
    is_current: bool
    sequence_no: int
    replayed: bool


@dataclass(frozen=True)
class SearchProjectionDocument:
    document_id: str
    work_id: str
    revision_id: str
    projection_kind: str
    text_object_id: str
    input_fingerprint: str
    sequence_no: int


@dataclass(frozen=True)
class SearchProjectionRow:
    document_id: str
    title: str
    abstract: str
    explanation: str


@dataclass(frozen=True)
class SearchProjectionRebuildResult:
    document_count: int
    document_ids: tuple[str, ...]
