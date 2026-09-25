from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_harvest import CrossrefPendingItem
from libs.scholarly_catalog.dtos.crossref_provider_revision import (
    CrossrefProviderProjection,
)


class ProjectCrossrefPendingItemPort(Protocol):
    def __call__(
        self,
        pending: CrossrefPendingItem,
        *,
        observed_at: datetime,
    ) -> CrossrefProviderProjection: ...
