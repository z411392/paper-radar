from datetime import datetime

from libs.scholarly_catalog.dtos.work_relation_result import WorkRelationResult
from libs.scholarly_catalog.ports.paper_identity_store_port import PaperIdentityStorePort


class RevokePaperWorkAlias:
    def __init__(self, store: PaperIdentityStorePort) -> None:
        self._store = store

    def __call__(
        self,
        alias_work_id: str,
        evidence_json: str,
        *,
        revoked_at: datetime,
    ) -> WorkRelationResult:
        return self._store.revoke_alias(alias_work_id, evidence_json, revoked_at)
