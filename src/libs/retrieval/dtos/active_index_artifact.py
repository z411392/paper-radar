from dataclasses import dataclass, field

from libs.retrieval.dtos.active_index import ActiveIndexPin


@dataclass(frozen=True)
class ActiveIndexArtifacts:
    pin: ActiveIndexPin
    index_bytes: bytes = field(repr=False)
    manifest_bytes: bytes = field(repr=False)
    embedding_ids: tuple[int, ...] = ()
