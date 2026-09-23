from datetime import datetime

from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment
from libs.watch_profiles.dtos.relevance_persistence import PersistedRelevanceAssessment
from libs.watch_profiles.ports.relevance_assessment_store_port import RelevanceAssessmentStorePort


class PersistRelevanceAssessment:
    def __init__(self, store: RelevanceAssessmentStorePort) -> None:
        self._store = store

    def __call__(
        self,
        assessment: RelevanceAssessment,
        *,
        assessed_at: datetime,
    ) -> PersistedRelevanceAssessment:
        return self._store.persist(assessment, assessed_at=assessed_at)
