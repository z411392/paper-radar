from datetime import datetime
from typing import Protocol

from libs.research_workflow.dtos.source_catalog_projection import (
    SourceCatalogProjectionResult,
)


class ProjectSourceCatalogUnitPort(Protocol):
    def __call__(
        self,
        source: str,
        unit_id: str,
        *,
        max_observations: int,
        projected_at: datetime,
    ) -> SourceCatalogProjectionResult: ...
