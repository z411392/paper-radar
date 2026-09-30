from datetime import datetime

from libs.scholarly_catalog.dtos.access_assessment import AccessAssessment
from libs.scholarly_catalog.ports.access_assessment_store_port import AccessAssessmentStorePort


class ReadCurrentAccessAssessment:
    def __init__(self, store: AccessAssessmentStorePort) -> None:
        self._store = store

    def __call__(self, manifestation_id: str, at: datetime) -> AccessAssessment:
        return self._store.read_current(manifestation_id, at)
