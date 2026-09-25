import hashlib
import json

from libs.discovery.dtos.pubmed_observation_replay import PubmedObservationReplay
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.dtos.abstract_evidence import AbstractEvidenceRequest
from libs.scholarly_catalog.dtos.paper_identity_observation import (
    PaperIdentityObservation,
)
from libs.scholarly_catalog.dtos.pubmed_catalog_projection import (
    PubmedCatalogProjection,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError
from libs.scholarly_catalog.ports.prepare_abstract_evidence_port import (
    PrepareAbstractEvidencePort,
)
from libs.scholarly_catalog.ports.record_paper_revision_port import (
    RecordPaperRevisionPort,
)
from libs.scholarly_catalog.ports.resolve_paper_identity_port import (
    ResolvePaperIdentityPort,
)


class ProjectPubmedObservation:
    def __init__(
        self,
        resolve: ResolvePaperIdentityPort,
        record_revision: RecordPaperRevisionPort,
        abstract_evidence: PrepareAbstractEvidencePort | None = None,
    ) -> None:
        self._resolve = resolve
        self._record_revision = record_revision
        self._abstract_evidence = abstract_evidence

    @staticmethod
    def _fingerprint(replay: PubmedObservationReplay) -> str:
        record = replay.record
        payload = {
            "pmid": record.pmid,
            "title": record.title,
            "abstract": record.abstract,
            "authors": list(record.authors),
            "journal_title": record.journal_title,
            "publication_date": record.publication_date,
            "publication_date_precision": record.publication_date_precision,
            "doi": record.doi,
            "pmcid": record.pmcid,
            "languages": list(record.languages),
            "publication_types": list(record.publication_types),
        }
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
            raise PaperIdentityError("invalid_pubmed_projection") from None
        return hashlib.sha256(encoded).hexdigest()

    def __call__(
        self,
        replay: PubmedObservationReplay,
    ) -> PubmedCatalogProjection:
        if not isinstance(replay, PubmedObservationReplay):
            raise PaperIdentityError("invalid_pubmed_projection")
        record = replay.record
        normalized = NormalizePaperIdentifier()("pmid", record.pmid)
        if normalized.normalized_value != record.pmid:
            raise PaperIdentityError("invalid_pubmed_projection")
        fingerprint = self._fingerprint(replay)
        observation = PaperIdentityObservation(
            source_observation_id=replay.observation_id,
            identifier_namespace="pmid",
            identifier_value=record.pmid,
            title=record.title,
            content_fingerprint=fingerprint,
            manifestation_kind="publication",
            landing_url=(
                "https://pubmed.ncbi.nlm.nih.gov/" + record.pmid + "/"
            ),
            publication_status="published",
            observed_at=replay.observed_at,
            source_updated_at=None,
            published_at=None,
        )
        resolution = self._resolve(observation)
        if (
            resolution.identifier_namespace != "pmid"
            or resolution.normalized_identifier != record.pmid
            or resolution.native_version is not None
        ):
            raise PaperIdentityError("pubmed_projection_identity_mismatch")
        evidence = {
            "format_version": 1,
            "source": "pubmed",
            "manifestation_id": resolution.manifestation_id,
            "revision_id": resolution.revision_id,
            "identifier_namespace": "pmid",
            "normalized_identifier": resolution.normalized_identifier,
            "content_fingerprint": fingerprint,
            "publication_date": record.publication_date,
            "publication_date_precision": record.publication_date_precision,
            "doi": record.doi,
            "pmcid": record.pmcid,
        }
        event_id = self._record_revision(
            work_id=resolution.work_id,
            revision_id=resolution.revision_id,
            event_kind="revision_available",
            source_evidence_id=resolution.revision_id,
            source_evidence=evidence,
            occurred_at=None,
            observed_at=replay.observed_at,
        )
        evidence_state = "not_configured"
        evidence_snapshot_id = None
        if self._abstract_evidence is not None:
            evidence = self._abstract_evidence(
                AbstractEvidenceRequest(
                    resolution.revision_id,
                    resolution.work_id,
                    replay.parser_version,
                    record.abstract,
                    replay.observed_at,
                )
            )
            if (
                evidence.revision_id != resolution.revision_id
                or evidence.work_id != resolution.work_id
                or evidence.state not in {"available", "unavailable"}
            ):
                raise PaperIdentityError("pubmed_evidence_projection_mismatch")
            evidence_state = evidence.state
            evidence_snapshot_id = evidence.snapshot_id
        return PubmedCatalogProjection(
            replay.observation_id,
            resolution.work_id,
            resolution.canonical_work_id,
            resolution.manifestation_id,
            resolution.revision_id,
            event_id,
            fingerprint,
            resolution.created_work,
            resolution.created_manifestation,
            resolution.created_revision,
            evidence_state,
            evidence_snapshot_id,
        )
