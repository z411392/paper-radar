from datetime import datetime

from libs.scholarly_catalog.dtos.work_alias_result import WorkAliasResult
from libs.scholarly_catalog.ports.paper_identity_store_port import PaperIdentityStorePort


class MergePaperWorkAlias:
    def __init__(self, store: PaperIdentityStorePort) -> None:
        self._store = store

    def __call__(
        self,
        alias_work_id: str,
        canonical_work_id: str,
        evidence_json: str,
        *,
        decided_at: datetime,
    ) -> WorkAliasResult:
        return self._store.merge_alias(alias_work_id, canonical_work_id, evidence_json, decided_at)
