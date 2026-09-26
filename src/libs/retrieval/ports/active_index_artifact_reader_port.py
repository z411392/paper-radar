from typing import Protocol

from libs.retrieval.dtos.active_index import ActiveIndexPin
from libs.retrieval.dtos.active_index_artifact import ActiveIndexArtifacts


class ActiveIndexArtifactReaderPort(Protocol):
    def read(self, pin: ActiveIndexPin) -> ActiveIndexArtifacts: ...
