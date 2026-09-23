from typing import Protocol

from libs.scholarly_catalog.dtos.normalized_identifier import NormalizedIdentifier
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.dtos.paper_identity_resolution import PaperIdentityResolution
from libs.scholarly_catalog.dtos.paper_identity_view import PaperIdentityView
from libs.scholarly_catalog.dtos.work_alias_result import WorkAliasResult
from libs.scholarly_catalog.dtos.work_relation_result import WorkRelationResult


class PaperIdentityStorePort(Protocol):
    def register(
        self,
        observation: PaperIdentityObservation,
        identifier: NormalizedIdentifier,
    ) -> PaperIdentityResolution: ...

    def read(self, identifier: NormalizedIdentifier) -> PaperIdentityView: ...

    def merge_alias(
        self,
        alias_work_id: str,
        canonical_work_id: str,
        evidence_json: str,
        decided_at: object,
    ) -> WorkAliasResult: ...

    def revoke_alias(
        self,
        alias_work_id: str,
        evidence_json: str,
        revoked_at: object,
    ) -> WorkRelationResult: ...

    def record_relation(
        self,
        source_work_id: str,
        target_work_id: str,
        relation_type: str,
        evidence_json: str,
        observed_at: object,
    ) -> WorkRelationResult: ...
