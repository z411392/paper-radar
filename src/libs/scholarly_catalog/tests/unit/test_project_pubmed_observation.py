from dataclasses import replace
from datetime import datetime, timezone

from libs.discovery.dtos.pubmed_bibliography_record import PubmedBibliographyRecord
from libs.discovery.dtos.pubmed_observation_replay import PubmedObservationReplay
from libs.scholarly_catalog.application.commands.project_pubmed_observation import (
    ProjectPubmedObservation,
)
from libs.scholarly_catalog.dtos.paper_identity_resolution import (
    PaperIdentityResolution,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _record() -> PubmedBibliographyRecord:
    return PubmedBibliographyRecord(
        pmid="100",
        title="Reliable PubMed Systems",
        abstract="PubMed abstract.",
        authors=("Example Alice",),
        journal_title="Journal",
        publication_date="2026-Sep-20",
        publication_date_precision="day",
        doi="10.1234/example",
        pmcid="PMC12345",
        languages=("eng",),
        publication_types=("Journal Article",),
    )


def _replay(observation: str = "observation:" + "a" * 64):
    return PubmedObservationReplay(
        observation_id=observation,
        unit_id="unit:pubmed",
        payload_object_id="raw:" + "b" * 64,
        parser_version="pubmed-eutils-parser-v1",
        observed_at=NOW,
        record=_record(),
    )


class Resolver:
    def __init__(self) -> None:
        self.observations = []

    def __call__(self, observation):
        self.observations.append(observation)
        return PaperIdentityResolution(
            "work:pubmed",
            "work:pubmed",
            "manifestation:pubmed",
            "revision:" + observation.content_fingerprint,
            "pmid",
            "100",
            None,
            True,
            True,
            True,
        )


class Recorder:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return "event:" + kwargs["source_evidence_id"]


def test_pubmed_projection_uses_pmid_without_inflating_date_precision() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectPubmedObservation(resolver, recorder)

    result = project(_replay())

    observed = resolver.observations[0]
    assert observed.identifier_namespace == "pmid"
    assert observed.identifier_value == "100"
    assert observed.manifestation_kind == "publication"
    assert observed.publication_status == "published"
    assert observed.published_at is None
    assert observed.source_updated_at is None
    event = recorder.calls[0]
    assert event["source_evidence_id"] == result.revision_id
    assert event["event_kind"] == "new_work"
    assert event["occurred_at"] is None
    assert event["source_evidence"]["publication_date"] == "2026-Sep-20"
    assert event["source_evidence"]["publication_date_precision"] == "day"
    assert event["source_evidence"]["doi"] == "10.1234/example"
    assert event["source_evidence"]["pmcid"] == "PMC12345"


def test_pubmed_metadata_change_creates_different_fingerprint() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectPubmedObservation(resolver, recorder)

    project(_replay())
    first = resolver.observations[-1].content_fingerprint
    project(
        replace(
            _replay("observation:" + "c" * 64),
            record=replace(_record(), journal_title="Other Journal"),
        )
    )
    second = resolver.observations[-1].content_fingerprint

    assert first != second


def test_equivalent_pubmed_observation_uses_revision_event_identity() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectPubmedObservation(resolver, recorder)

    first = project(_replay())
    second = project(_replay("observation:" + "c" * 64))

    assert first.revision_id == second.revision_id
    assert recorder.calls[0]["source_evidence_id"] == first.revision_id
    assert recorder.calls[1]["source_evidence_id"] == second.revision_id
    assert recorder.calls[0]["source_evidence"] == recorder.calls[1]["source_evidence"]
