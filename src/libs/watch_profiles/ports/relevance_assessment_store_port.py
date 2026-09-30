from datetime import datetime
from typing import Protocol

from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment
from libs.watch_profiles.dtos.relevance_persistence import PersistedRelevanceAssessment


class RelevanceAssessmentStorePort(Protocol):
    def persist(
        self,
        assessment: RelevanceAssessment,
        *,
        assessed_at: datetime,
    ) -> PersistedRelevanceAssessment: ...
