from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ActivateIndexInput:
    space_id: str
    generation_id: str
    expected_generation_id: str | None
    expected_pointer_version: int | None


@dataclass(frozen=True)
class ActiveIndexPin:
    space_id: str
    generation_id: str
    pointer_version: int
    relative_directory: str
    index_sha256: str
    manifest_sha256: str
    membership_digest: str
    document_high_watermark: int
    vector_count: int
    activated_at: datetime
    replayed: bool = False
