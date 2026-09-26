from typing import Protocol

from libs.retrieval.dtos.active_index import ActiveIndexPin
from libs.retrieval.dtos.active_index_artifact import ActiveIndexArtifacts
from libs.retrieval.dtos.index_generation import IndexGenerationInput


class RebuildActiveIndexArtifactsPort(Protocol):
    def __call__(
        self,
        pin: ActiveIndexPin,
        config: IndexGenerationInput,
    ) -> ActiveIndexArtifacts: ...
