from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from libs.discovery.dtos.arxiv_observation_replay import ArxivObservationReplay
from libs.discovery.dtos.arxiv_source_record import ArxivSourceRecord
from libs.scholarly_catalog.application.commands.project_arxiv_observation import (
    ProjectArxivObservation,
)
from libs.scholarly_catalog.dtos.paper_identity_resolution import (
    PaperIdentityResolution,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _record(version: int = 1) -> ArxivSourceRecord:
    return ArxivSourceRecord(
        source_record_id=f"2501.12345v{version}",
        source_url=f"https://arxiv.org/abs/2501.12345v{version}",
        arxiv_id="2501.12345",
        version=version,
        title="Reliable Systems",
        abstract="An abstract.",
        authors=(("Alice Example", ()),),
        published_at=datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 25, 11, 0, tzinfo=timezone.utc),
        categories=(("cs.SE", "http://arxiv.org/schemas/atom"),),
        primary_category="cs.SE",
        doi="10.1234/example",
        journal_reference=None,
        comment=None,
        links=(),
        missing_fields=(),
    )


def _replay(version: int = 1) -> ArxivObservationReplay:
    return ArxivObservationReplay(
        observation_id="observation:" + "a" * 64,
        unit_id="unit:fixture",
        payload_object_id="raw:" + "b" * 64,
        parser_version="arxiv-atom-v1",
        observed_at=NOW,
        record=_record(version),
    )


class Resolver:
    def __init__(self) -> None:
        self.observations = []

    def __call__(self, observation):
        self.observations.append(observation)
        return PaperIdentityResolution(
            "work:fixture",
            "work:fixture",
            "manifestation:fixture",
            "revision:fixture:" + observation.content_fingerprint,
            "arxiv",
            "2501.12345",
            "1" if observation.identifier_value.endswith("v1") else "2",
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


def test_projection_maps_arxiv_identity_and_revision_event() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectArxivObservation(resolver, recorder)

    result = project(_replay())

    observed = resolver.observations[0]
    assert observed.identifier_namespace == "arxiv"
    assert observed.identifier_value == "2501.12345v1"
    assert observed.manifestation_kind == "preprint"
    assert observed.publication_status == "preprint"
    assert observed.source_updated_at == _record().updated_at
    assert observed.published_at == _record().published_at
    assert result.revision_id.startswith("revision:fixture:")
    event = recorder.calls[0]
    assert event["event_kind"] == "new_work"
    assert event["source_evidence_id"] == result.revision_id
    assert event["source_evidence"]["revision_id"] == result.revision_id
    assert event["source_evidence"]["doi"] == "10.1234/example"


def test_native_version_changes_fingerprint_even_when_content_is_same() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectArxivObservation(resolver, recorder)

    project(_replay(1))
    first = resolver.observations[-1].content_fingerprint
    second_replay = replace(
        _replay(1),
        record=replace(
            _record(1),
            source_record_id="2501.12345v2",
            source_url="https://arxiv.org/abs/2501.12345v2",
            version=2,
        ),
    )
    project(second_replay)
    second = resolver.observations[-1].content_fingerprint

    assert first != second


def test_equivalent_observation_uses_revision_identity_for_event_replay() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectArxivObservation(resolver, recorder)
    replay = _replay()

    first = project(replay)
    second = project(
        replace(
            replay,
            observation_id="observation:" + "c" * 64,
            observed_at=NOW,
        )
    )

    assert first.revision_id == second.revision_id
    assert recorder.calls[0]["source_evidence_id"] == first.revision_id
    assert recorder.calls[1]["source_evidence_id"] == first.revision_id
    assert recorder.calls[0]["source_evidence"] == recorder.calls[1]["source_evidence"]


def test_future_source_occurrence_fails_before_identity_side_effect() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectArxivObservation(resolver, recorder)
    replay = replace(
        _replay(),
        record=replace(
            _record(),
            published_at=NOW + timedelta(minutes=1),
        ),
    )

    with pytest.raises(
        PaperIdentityError,
        match="invalid_arxiv_projection",
    ):
        project(replay)

    assert resolver.observations == []
    assert recorder.calls == []


def test_inconsistent_record_identity_fails_before_resolver_side_effect() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectArxivObservation(resolver, recorder)
    replay = replace(
        _replay(),
        record=replace(_record(), arxiv_id="2501.99999"),
    )

    with pytest.raises(PaperIdentityError, match="invalid_arxiv_projection"):
        project(replay)
    assert resolver.observations == []
    assert recorder.calls == []


def test_old_arxiv_observation_is_recorded_as_late_discovery() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectArxivObservation(resolver, recorder)
    old = replace(
        _replay(),
        record=replace(
            _record(),
            published_at=NOW - timedelta(days=3650),
            updated_at=NOW - timedelta(days=3650),
        ),
    )

    project(old)

    assert recorder.calls[0]["event_kind"] == "late_discovery"
    assert recorder.calls[0]["occurred_at"] == NOW - timedelta(days=3650)


def test_recent_arxiv_v1_is_recorded_as_new_work() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectArxivObservation(resolver, recorder)

    project(_replay(1))

    assert recorder.calls[0]["event_kind"] == "new_work"


def test_recent_arxiv_v2_is_recorded_as_revision_available() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectArxivObservation(resolver, recorder)
    replay = replace(
        _replay(2),
        record=replace(
            _record(2),
            source_record_id="2501.12345v2",
            source_url="https://arxiv.org/abs/2501.12345v2",
            version=2,
        ),
    )

    project(replay)

    assert recorder.calls[0]["event_kind"] == "revision_available"


def test_fourteen_day_boundary_is_still_current_not_late() -> None:
    resolver = Resolver()
    recorder = Recorder()
    project = ProjectArxivObservation(resolver, recorder)
    boundary = replace(
        _replay(),
        record=replace(
            _record(),
            published_at=NOW - timedelta(days=14),
            updated_at=NOW - timedelta(days=14),
        ),
    )

    project(boundary)

    assert recorder.calls[0]["event_kind"] == "new_work"
