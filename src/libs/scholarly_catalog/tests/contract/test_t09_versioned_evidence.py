import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject
from libs.scholarly_catalog.adapters.driven.kernel_evidence_object_adapter import (
    KernelEvidenceObjectAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_evidence_snapshot_store_adapter import (
    SqliteEvidenceSnapshotStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_paper_identity_store_adapter import (
    SqlitePaperIdentityStoreAdapter,
)
from libs.scholarly_catalog.application.commands.prepare_evidence_snapshot import PrepareEvidenceSnapshot
from libs.scholarly_catalog.application.commands.resolve_paper_identity import ResolvePaperIdentity
from libs.scholarly_catalog.application.queries.read_evidence_snapshot import ReadEvidenceSnapshot
from libs.scholarly_catalog.domain.services.evidence_snapshot_rules import EvidenceSnapshotRules
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import NormalizePaperIdentifier
from libs.scholarly_catalog.dtos.evidence_anchor_request import EvidenceAnchorRequest
from libs.scholarly_catalog.dtos.evidence_preparation_input import EvidencePreparationInput
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import EvidenceSnapshotError

AT = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)


def _tools(tmp_path: Path):
    root = tmp_path / "workspace"
    migrations = load_workspace_migrations(with_discovery=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=3)
    identity = ResolvePaperIdentity(
        NormalizePaperIdentifier(),
        SqlitePaperIdentityStoreAdapter(schema.connect),
    )(
        PaperIdentityObservation(
            "obs:evidence:v1",
            "arxiv",
            "2609.00031v1",
            "Evidence fixture",
            "e" * 64,
            "preprint",
            "https://arxiv.org/abs/2609.00031",
            "preprint",
            AT,
            AT,
            AT,
        )
    )
    factory = SqliteConnectionFactory(root)
    uow = SqliteObjectUnitOfWorkAdapter(factory)
    files = FilesystemObjectBytesAdapter(root)
    objects = KernelEvidenceObjectAdapter(
        PublishObject(files, uow),
        ReadObject(files, uow),
    )
    snapshots = SqliteEvidenceSnapshotStoreAdapter(schema.connect)
    rules = EvidenceSnapshotRules()
    return (
        root,
        identity,
        objects,
        snapshots,
        PrepareEvidenceSnapshot(rules, objects, snapshots),
        ReadEvidenceSnapshot(rules, objects, snapshots),
    )


def _input(
    revision_id: str,
    work_id: str,
    *,
    source: bytes = b"abstract: result 42",
    text: str | None = "Abstract\nResult was 42 units.",
    scope: str = "abstract",
    complete: bool = False,
    included: tuple[str, ...] = ("abstract",),
    missing: tuple[str, ...] = (),
    parser_error: str | None = None,
    anchors: tuple[EvidenceAnchorRequest, ...] = (
        EvidenceAnchorRequest("42 units", 20, 28, "abstract", "p1"),
    ),
) -> EvidencePreparationInput:
    return EvidencePreparationInput(
        revision_id,
        work_id,
        source,
        "text/plain",
        "fixture-parser-v1",
        text,
        scope,
        complete,
        included,
        missing,
        parser_error,
        anchors,
        AT,
    )


def test_abstract_snapshot_saves_raw_text_hash_coverage_and_exact_anchor(tmp_path: Path) -> None:
    _, identity, _, _, prepare, read = _tools(tmp_path)
    snapshot = prepare(_input(identity.revision_id, identity.work_id))
    reopened = read(snapshot.snapshot_id)

    assert snapshot.evidence_level == "abstract_only"
    assert snapshot.parser_version == "fixture-parser-v1"
    assert reopened.source_bytes == b"abstract: result 42"
    assert reopened.normalized_text == "Abstract\nResult was 42 units."
    assert reopened.snapshot.fingerprint == snapshot.fingerprint
    assert len(reopened.snapshot.anchors) == 1
    anchor = reopened.snapshot.anchors[0]
    assert reopened.normalized_text[anchor.offset_start : anchor.offset_end] == anchor.quote
    assert anchor.quote == "42 units"
    assert anchor.snapshot_id == snapshot.snapshot_id


def test_complete_document_becomes_full_text_but_missing_section_stays_selected(tmp_path: Path) -> None:
    _, identity, _, _, prepare, _ = _tools(tmp_path)
    full = prepare(
        _input(
            identity.revision_id,
            identity.work_id,
            source=b"full",
            text="Methods\nResult",
            scope="document",
            complete=True,
            included=("methods", "results"),
            anchors=(EvidenceAnchorRequest("Result", 8, 14, "results", "p2"),),
        )
    )
    selected = prepare(
        _input(
            identity.revision_id,
            identity.work_id,
            source=b"partial",
            text="Methods only",
            scope="document",
            complete=False,
            included=("methods",),
            missing=("results",),
            anchors=(EvidenceAnchorRequest("Methods", 0, 7, "methods", "p1"),),
        )
    )
    assert full.evidence_level == "full_text"
    assert selected.evidence_level == "selected_sections"


def test_parser_failure_creates_no_evidence_objects_or_snapshot(tmp_path: Path) -> None:
    root, identity, _, _, prepare, _ = _tools(tmp_path)
    connection = sqlite3.connect(root / "state/app.sqlite3")
    try:
        before_objects = connection.execute("SELECT COUNT(*) FROM object_registry").fetchone()[0]
        before_snapshots = connection.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0]
    finally:
        connection.close()

    with pytest.raises(EvidenceSnapshotError, match="evidence_parse_failed"):
        prepare(
            _input(
                identity.revision_id,
                identity.work_id,
                text=None,
                parser_error="parser_crashed",
                anchors=(),
            )
        )

    connection = sqlite3.connect(root / "state/app.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM object_registry").fetchone()[0] == before_objects
        assert connection.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == before_snapshots
    finally:
        connection.close()


@pytest.mark.parametrize(
    "anchor",
    [
        EvidenceAnchorRequest("wrong", 20, 28, "abstract", "p1"),
        EvidenceAnchorRequest("42 units", 19, 27, "abstract", "p1"),
        EvidenceAnchorRequest("42 units", 20, 28, "results", "p1"),
    ],
)
def test_invalid_anchor_never_creates_snapshot(tmp_path: Path, anchor: EvidenceAnchorRequest) -> None:
    root, identity, _, _, prepare, _ = _tools(tmp_path)
    with pytest.raises(EvidenceSnapshotError):
        prepare(_input(identity.revision_id, identity.work_id, anchors=(anchor,)))
    connection = sqlite3.connect(root / "state/app.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 0
    finally:
        connection.close()


def test_exact_replay_is_idempotent_and_reopens_same_snapshot(tmp_path: Path) -> None:
    _, identity, _, _, prepare, read = _tools(tmp_path)
    request = _input(identity.revision_id, identity.work_id)
    first = prepare(request)
    second = prepare(request)
    assert first == second
    assert read(first.snapshot_id).snapshot == first


def test_anchor_id_cannot_be_reused_as_if_it_belonged_to_another_snapshot(tmp_path: Path) -> None:
    _, identity, _, _, prepare, read = _tools(tmp_path)
    first = prepare(_input(identity.revision_id, identity.work_id))
    second = prepare(
        _input(
            identity.revision_id,
            identity.work_id,
            source=b"abstract changed",
            text="Abstract\nResult was 43 units.",
            anchors=(EvidenceAnchorRequest("43 units", 20, 28, "abstract", "p1"),),
        )
    )
    first_anchor = first.anchors[0]
    reopened_second = read(second.snapshot_id).snapshot
    assert first.snapshot_id != second.snapshot_id
    assert first_anchor.anchor_id not in {anchor.anchor_id for anchor in reopened_second.anchors}


def test_full_text_claim_is_rejected_when_parser_reports_missing_required_sections(
    tmp_path: Path,
) -> None:
    _, identity, _, _, prepare, _ = _tools(tmp_path)
    with pytest.raises(EvidenceSnapshotError, match="invalid_evidence_coverage"):
        prepare(
            _input(
                identity.revision_id,
                identity.work_id,
                scope="document",
                complete=True,
                included=("methods",),
                missing=("results",),
                anchors=(EvidenceAnchorRequest("Abstract", 0, 8, None, None),),
            )
        )
