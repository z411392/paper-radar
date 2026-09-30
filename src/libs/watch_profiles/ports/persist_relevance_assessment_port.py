from datetime import datetime
from typing import Protocol

from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment
from libs.watch_profiles.dtos.relevance_persistence import PersistedRelevanceAssessment


class PersistRelevanceAssessmentPort(Protocol):
    def __call__(
        self,
        assessment: RelevanceAssessment,
        *,
        assessed_at: datetime,
    ) -> PersistedRelevanceAssessment: ...
