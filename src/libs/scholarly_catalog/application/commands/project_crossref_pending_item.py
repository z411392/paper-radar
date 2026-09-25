from datetime import datetime

from libs.discovery.dtos.crossref_harvest import CrossrefPendingItem
from libs.scholarly_catalog.domain.services.crossref_integrity_rules import (
    CrossrefIntegrityRules,
)
from libs.scholarly_catalog.domain.services.crossref_provider_revision_rules import (
    CrossrefProviderRevisionRules,
)
from libs.scholarly_catalog.domain.services.crossref_relation_rules import (
    CrossrefRelationRules,
)
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityAssertionDraft,
    CrossrefIntegrityGapDraft,
)
from libs.scholarly_catalog.dtos.crossref_provider_revision import (
    CrossrefProviderProjection,
)
from libs.scholarly_catalog.dtos.crossref_relation_assertion import (
    CrossrefRelationAssertionDraft,
    CrossrefRelationGapDraft,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError
from libs.scholarly_catalog.ports.bind_crossref_integrity_works_port import (
    BindCrossrefIntegrityWorksPort,
)
from libs.scholarly_catalog.ports.crossref_integrity_store_port import (
    CrossrefIntegrityStorePort,
)
from libs.scholarly_catalog.ports.crossref_provider_revision_store_port import (
    CrossrefProviderRevisionStorePort,
)
from libs.scholarly_catalog.ports.crossref_relation_store_port import (
    CrossrefRelationStorePort,
)
from libs.scholarly_catalog.ports.promote_crossref_integrity_events_port import (
    PromoteCrossrefIntegrityEventsPort,
)


class ProjectCrossrefPendingItem:
    def __init__(
        self,
        normalize: NormalizePaperIdentifier,
        store: CrossrefProviderRevisionStorePort,
        relations: CrossrefRelationStorePort,
        integrity: CrossrefIntegrityStorePort | None = None,
        integrity_bindings: BindCrossrefIntegrityWorksPort | None = None,
        integrity_events: PromoteCrossrefIntegrityEventsPort | None = None,
    ) -> None:
        self._normalize = normalize
        self._store = store
        self._relations = relations
        self._integrity = integrity
        self._integrity_bindings = integrity_bindings
        self._integrity_events = integrity_events

    def __call__(
        self,
        pending: CrossrefPendingItem,
        *,
        observed_at: datetime,
    ) -> CrossrefProviderProjection:
        if not isinstance(pending, CrossrefPendingItem) or pending.raw_doi is None:
            raise CrossrefProviderProjectionError("crossref_provider_doi_invalid")
        try:
            identifier = self._normalize("doi", pending.raw_doi)
        except PaperIdentityError as exc:
            raise CrossrefProviderProjectionError(
                "crossref_provider_doi_invalid"
            ) from exc
        item = CrossrefProviderRevisionRules.item(pending)
        draft = CrossrefProviderRevisionRules.draft(
            pending,
            canonical_doi=identifier.normalized_value,
            observed_at=observed_at,
        )
        result = self._store.register(draft)
        entries, gaps = CrossrefRelationRules.extract(item)

        target_namespaces = {
            "doi": "doi",
            "pmid": "pmid",
            "pmcid": "pmc",
            "arxiv": "arxiv",
        }
        assertions = []
        for entry in entries:
            relation_type = "".join(
                chr(ord(char) + 32) if "A" <= char <= "Z" else char
                for char in entry.target_id_type_raw
            )
            namespace = target_namespaces.get(relation_type)
            normalized_namespace = normalized_value = None
            normalization_state = "raw"
            if namespace is not None:
                try:
                    target = self._normalize(namespace, entry.target_value_raw)
                except PaperIdentityError:
                    normalization_state = "invalid"
                else:
                    normalized_namespace = target.namespace
                    normalized_value = target.normalized_value
                    normalization_state = "normalized"
            assertions.append(
                CrossrefRelationAssertionDraft(
                    result.canonical_doi,
                    result.provider_revision_id,
                    entry.ordinal,
                    entry.predicate_raw,
                    entry.target_id_type_raw,
                    entry.target_value_raw,
                    entry.asserted_by_raw,
                    entry.relation_class,
                    normalized_namespace,
                    normalized_value,
                    normalization_state,
                    observed_at,
                )
            )
        gap_drafts = tuple(
            CrossrefRelationGapDraft(
                result.provider_revision_id,
                gap.path,
                gap.error_code,
                gap.raw_json,
                observed_at,
            )
            for gap in gaps
        )
        self._relations.register(tuple(assertions), gap_drafts)

        if self._integrity is not None:
            integrity_entries, integrity_gaps = CrossrefIntegrityRules.extract(item)
            integrity_assertions = []
            for entry in integrity_entries:
                counterparty_raw = entry.counterparty_doi_raw
                if counterparty_raw is None:
                    counterparty_canonical = None
                    counterparty_state = "missing"
                else:
                    try:
                        counterparty = self._normalize("doi", counterparty_raw)
                    except PaperIdentityError:
                        counterparty_canonical = None
                        counterparty_state = "invalid"
                    else:
                        counterparty_canonical = counterparty.normalized_value
                        counterparty_state = "normalized"
                if entry.wire_direction == "update_to":
                    notice_doi = result.canonical_doi
                    target_doi = counterparty_canonical
                else:
                    notice_doi = counterparty_canonical
                    target_doi = result.canonical_doi
                integrity_assertions.append(
                    CrossrefIntegrityAssertionDraft(
                        result.canonical_doi,
                        result.provider_revision_id,
                        entry.wire_direction,
                        entry.ordinal,
                        counterparty_raw,
                        counterparty_canonical,
                        counterparty_state,
                        notice_doi,
                        target_doi,
                        entry.type_raw,
                        entry.source_raw,
                        entry.label_raw,
                        entry.record_id_raw_json,
                        entry.event_class,
                        entry.updated_value,
                        entry.updated_precision,
                        entry.updated_raw_json,
                        entry.raw_json,
                        observed_at,
                    )
                )
            integrity_gap_drafts = tuple(
                CrossrefIntegrityGapDraft(
                    result.provider_revision_id,
                    gap.path,
                    gap.error_code,
                    gap.raw_json,
                    observed_at,
                )
                for gap in integrity_gaps
            )
            integrity_refs = self._integrity.register(
                tuple(integrity_assertions),
                integrity_gap_drafts,
            )
            if self._integrity_bindings is not None:
                self._integrity_bindings(
                    integrity_refs,
                    observed_at=observed_at,
                )
            if self._integrity_events is not None:
                self._integrity_events(integrity_refs)
        return result
