from datetime import datetime

from libs.scholarly_catalog.dtos.work_relation_result import WorkRelationResult
from libs.scholarly_catalog.ports.paper_identity_store_port import PaperIdentityStorePort


class RecordPaperWorkRelation:
    def __init__(self, store: PaperIdentityStorePort) -> None:
        self._store = store

    def __call__(
        self,
        source_work_id: str,
        target_work_id: str,
        relation_type: str,
        evidence_json: str,
        *,
        observed_at: datetime,
    ) -> WorkRelationResult:
        return self._store.record_relation(
            source_work_id,
            target_work_id,
            relation_type,
            evidence_json,
            observed_at,
        )
