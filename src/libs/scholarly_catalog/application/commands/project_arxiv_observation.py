import hashlib
import json
from datetime import timedelta

from libs.discovery.dtos.arxiv_observation_replay import ArxivObservationReplay
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.dtos.abstract_evidence import AbstractEvidenceRequest
from libs.scholarly_catalog.dtos.arxiv_catalog_projection import (
    ArxivCatalogProjection,
)
from libs.scholarly_catalog.dtos.paper_identity_observation import (
    PaperIdentityObservation,
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


class ProjectArxivObservation:
    BACKFILL_WINDOW = timedelta(days=14)

    def __init__(
        self,
        resolve: ResolvePaperIdentityPort,
        record_revision: RecordPaperRevisionPort,
        abstract_evidence: PrepareAbstractEvidencePort | None = None,
    ) -> None:
        self._resolve = resolve
        self._record_revision = record_revision
        self._abstract_evidence = abstract_evidence

    @classmethod
    def _event(cls, replay: ArxivObservationReplay) -> tuple[str, object]:
        record = replay.record
        occurrence = (
            record.published_at
            if record.version in {None, 1}
            else record.updated_at
        )
        if (
            occurrence.tzinfo is None
            or occurrence.utcoffset() is None
            or replay.observed_at.tzinfo is None
            or replay.observed_at.utcoffset() is None
        ):
            raise PaperIdentityError("invalid_arxiv_projection")
        age = replay.observed_at - occurrence
        if age < timedelta(0):
            raise PaperIdentityError("invalid_arxiv_projection")
        if age > cls.BACKFILL_WINDOW:
            return ("late_discovery", occurrence)
        if record.version in {None, 1}:
            return ("new_work", occurrence)
        return ("revision_available", occurrence)

    @staticmethod
    def _fingerprint(value: ArxivObservationReplay) -> str:
        record = value.record
        payload = {
            "arxiv_base": record.arxiv_id,
            "native_version": record.version,
            "title": record.title,
            "abstract": record.abstract,
            "authors": [
                [name, list(affiliations)]
                for name, affiliations in record.authors
            ],
            "categories": [
                [term, scheme]
                for term, scheme in record.categories
            ],
            "primary_category": record.primary_category,
            "doi": record.doi,
            "journal_reference": record.journal_reference,
            "comment": record.comment,
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
            raise PaperIdentityError("invalid_arxiv_projection") from None
        return hashlib.sha256(encoded).hexdigest()

    def __call__(
        self,
        replay: ArxivObservationReplay,
    ) -> ArxivCatalogProjection:
        if not isinstance(replay, ArxivObservationReplay):
            raise PaperIdentityError("invalid_arxiv_projection")
        record = replay.record
        normalized = NormalizePaperIdentifier()(
            "arxiv",
            record.source_record_id,
        )
        expected_version = (
            None if record.version is None else str(record.version)
        )
        if (
            normalized.normalized_value != record.arxiv_id
            or normalized.native_version != expected_version
        ):
            raise PaperIdentityError("invalid_arxiv_projection")
        fingerprint = self._fingerprint(replay)
        observation = PaperIdentityObservation(
            source_observation_id=replay.observation_id,
            identifier_namespace="arxiv",
            identifier_value=record.source_record_id,
            title=record.title,
            content_fingerprint=fingerprint,
            manifestation_kind="preprint",
            landing_url=record.source_url,
            publication_status="preprint",
            observed_at=replay.observed_at,
            source_updated_at=record.updated_at,
            published_at=record.published_at,
        )
        resolution = self._resolve(observation)
        if (
            resolution.identifier_namespace != "arxiv"
            or resolution.normalized_identifier != record.arxiv_id
            or resolution.native_version != expected_version
        ):
            raise PaperIdentityError("arxiv_projection_identity_mismatch")
        evidence = {
            "format_version": 1,
            "source": "arxiv",
            "manifestation_id": resolution.manifestation_id,
            "revision_id": resolution.revision_id,
            "identifier_namespace": "arxiv",
            "normalized_identifier": resolution.normalized_identifier,
            "native_version": resolution.native_version,
            "content_fingerprint": fingerprint,
            "doi": record.doi,
        }
        event_kind, occurred_at = self._event(replay)
        event_id = self._record_revision(
            work_id=resolution.work_id,
            revision_id=resolution.revision_id,
            event_kind=event_kind,
            source_evidence_id=resolution.revision_id,
            source_evidence=evidence,
            occurred_at=occurred_at,
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
                or (
                    evidence.state == "available"
                    and evidence.snapshot_id is None
                )
                or (
                    evidence.state == "unavailable"
                    and evidence.snapshot_id is not None
                )
            ):
                raise PaperIdentityError("arxiv_evidence_projection_mismatch")
            evidence_state = evidence.state
            evidence_snapshot_id = evidence.snapshot_id
        return ArxivCatalogProjection(
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
