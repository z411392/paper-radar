from datetime import datetime
from typing import Protocol

from libs.research_workflow.dtos.source_catalog_projection import (
    SourceCatalogProjectionProgress,
)


class SourceCatalogProjectionStorePort(Protocol):
    def ensure(
        self,
        unit_id: str,
        source: str,
        created_at: datetime,
    ) -> SourceCatalogProjectionProgress: ...

    def advance(
        self,
        unit_id: str,
        source: str,
        *,
        expected_checkpoint_version: int,
        expected_after_observation_id: str | None,
        observation_id: str,
        updated_at: datetime,
    ) -> SourceCatalogProjectionProgress: ...

    def complete(
        self,
        unit_id: str,
        source: str,
        *,
        expected_checkpoint_version: int,
        updated_at: datetime,
    ) -> SourceCatalogProjectionProgress: ...
