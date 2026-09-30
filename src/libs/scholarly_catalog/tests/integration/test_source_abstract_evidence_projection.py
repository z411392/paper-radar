from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from libs.discovery.dtos.arxiv_observation_replay import ArxivObservationReplay
from libs.discovery.dtos.arxiv_source_record import ArxivSourceRecord
from libs.discovery.dtos.pubmed_bibliography_record import PubmedBibliographyRecord
from libs.discovery.dtos.pubmed_observation_replay import PubmedObservationReplay
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import (
    FilesystemObjectBytesAdapter,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import (
    SqliteConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
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
from libs.scholarly_catalog.adapters.driven.sqlite_research_event_store_adapter import (
    SqliteResearchEventStoreAdapter,
)
from libs.scholarly_catalog.application.commands.prepare_abstract_evidence import (
    PrepareAbstractEvidence,
)
from libs.scholarly_catalog.application.commands.prepare_evidence_snapshot import (
    PrepareEvidenceSnapshot,
)
from libs.scholarly_catalog.application.commands.project_arxiv_observation import (
    ProjectArxivObservation,
)
from libs.scholarly_catalog.application.commands.project_pubmed_observation import (
    ProjectPubmedObservation,
)
from libs.scholarly_catalog.application.commands.record_paper_revision import (
    RecordPaperRevision,
)
from libs.scholarly_catalog.application.commands.resolve_paper_identity import (
    ResolvePaperIdentity,
)
from libs.scholarly_catalog.domain.services.evidence_snapshot_rules import (
    EvidenceSnapshotRules,
)
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _runtime(root: Path):
    migrations = load_workspace_migrations(with_runtime=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=3)
    raw = SqliteConnectionFactory(root)
    files = FilesystemObjectBytesAdapter(root)
    objects = SqliteObjectUnitOfWorkAdapter(raw)
    evidence_objects = KernelEvidenceObjectAdapter(
        PublishObject(files, objects),
        ReadObject(files, objects),
    )
    snapshots = SqliteEvidenceSnapshotStoreAdapter(schema.connect)
    prepare = PrepareAbstractEvidence(
        PrepareEvidenceSnapshot(
            EvidenceSnapshotRules(),
            evidence_objects,
            snapshots,
        )
    )
    identity = SqlitePaperIdentityStoreAdapter(schema.connect)
    resolver = ResolvePaperIdentity(NormalizePaperIdentifier(), identity)
    events = RecordPaperRevision(
        SqliteResearchEventStoreAdapter(schema.connect)
    )
    return schema, snapshots, prepare, resolver, events


def _arxiv_record() -> ArxivSourceRecord:
    return ArxivSourceRecord(
        source_record_id="2501.12345v1",
        source_url="https://arxiv.org/abs/2501.12345v1",
        arxiv_id="2501.12345",
        version=1,
        title="Reliable Systems",
        abstract="  line one\r\nline two  ",
        authors=(("Alice Example", ()),),
        published_at=datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 25, 11, 0, tzinfo=timezone.utc),
        categories=(("cs.SE", None),),
        primary_category="cs.SE",
        doi=None,
        journal_reference=None,
        comment=None,
        links=(),
        missing_fields=(),
    )


def test_arxiv_projection_replays_one_abstract_snapshot(
    tmp_path: Path,
) -> None:
    schema, snapshots, prepare, resolver, events = _runtime(
        tmp_path / "runtime"
    )
    project = ProjectArxivObservation(resolver, events, prepare)
    first_replay = ArxivObservationReplay(
        "observation:" + "a" * 64,
        "unit:arxiv",
        "raw:" + "b" * 64,
        "arxiv-atom-v1",
        NOW,
        _arxiv_record(),
    )
    second_replay = replace(
        first_replay,
        observation_id="observation:" + "c" * 64,
    )

    first = project(first_replay)
    second = project(second_replay)

    assert first.evidence_state == second.evidence_state == "available"
    assert first.evidence_snapshot_id == second.evidence_snapshot_id
    assert first.evidence_snapshot_id is not None
    snapshot = snapshots.read(first.evidence_snapshot_id)
    assert snapshot.evidence_level == "abstract_only"
    assert len(snapshot.anchors) == 1
    assert snapshot.anchors[0].quote == "line one\nline two"
    assert (
        snapshot.anchors[0].offset_start,
        snapshot.anchors[0].offset_end,
    ) == (0, len("line one\nline two"))

    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM evidence_snapshots"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM evidence_anchors"
        ).fetchone()[0] == 1
        kinds = connection.execute(
            "SELECT kind,count(*) FROM object_registry "
            "WHERE kind IN ('evidence','extracted') GROUP BY kind "
            "ORDER BY kind"
        ).fetchall()
        assert [tuple(row) for row in kinds] == [
            ("evidence", 1),
            ("extracted", 1),
        ]
    finally:
        connection.close()


def test_pubmed_missing_abstract_creates_no_evidence_objects(
    tmp_path: Path,
) -> None:
    schema, _, prepare, resolver, events = _runtime(tmp_path / "runtime")
    project = ProjectPubmedObservation(resolver, events, prepare)
    record = PubmedBibliographyRecord(
        pmid="100",
        title="No abstract paper",
        abstract=None,
        authors=("Example Alice",),
        journal_title="Journal",
        publication_date="2026",
        publication_date_precision="year",
        doi=None,
        pmcid=None,
        languages=("eng",),
        publication_types=("Journal Article",),
    )
    replay = PubmedObservationReplay(
        "observation:" + "d" * 64,
        "unit:pubmed",
        "raw:" + "e" * 64,
        "pubmed-eutils-parser-v1",
        NOW,
        record,
    )

    result = project(replay)

    assert result.evidence_state == "unavailable"
    assert result.evidence_snapshot_id is None
    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM evidence_snapshots"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM object_registry "
            "WHERE kind IN ('evidence','extracted')"
        ).fetchone()[0] == 0
    finally:
        connection.close()
