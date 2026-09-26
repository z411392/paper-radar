from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from libs.discovery.dtos.arxiv_observation_replay import ArxivObservationReplay
from libs.discovery.dtos.arxiv_source_record import ArxivSourceRecord
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_paper_identity_store_adapter import (
    SqlitePaperIdentityStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_research_event_store_adapter import (
    SqliteResearchEventStoreAdapter,
)
from libs.scholarly_catalog.application.commands.project_arxiv_observation import (
    ProjectArxivObservation,
)
from libs.scholarly_catalog.application.commands.record_paper_revision import (
    RecordPaperRevision,
)
from libs.scholarly_catalog.application.commands.resolve_paper_identity import (
    ResolvePaperIdentity,
)
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _record(version: int) -> ArxivSourceRecord:
    return ArxivSourceRecord(
        source_record_id=f"2501.12345v{version}",
        source_url=f"https://arxiv.org/abs/2501.12345v{version}",
        arxiv_id="2501.12345",
        version=version,
        title="Reliable Systems",
        abstract="Identical text across versions.",
        authors=(("Alice Example", ()),),
        published_at=datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 25, 11, 0, tzinfo=timezone.utc),
        categories=(("cs.SE", None),),
        primary_category="cs.SE",
        doi="10.1234/example",
        journal_reference=None,
        comment=None,
        links=(),
        missing_fields=(),
    )


def _replay(observation: str, version: int) -> ArxivObservationReplay:
    return ArxivObservationReplay(
        observation,
        "unit:catalog",
        "raw:" + "b" * 64,
        "arxiv-atom-v1",
        NOW,
        _record(version),
    )


def _projector(root: Path):
    migrations = load_workspace_migrations(with_runtime=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=3)
    identity = SqlitePaperIdentityStoreAdapter(schema.connect)
    return (
        schema,
        ProjectArxivObservation(
            ResolvePaperIdentity(NormalizePaperIdentifier(), identity),
            RecordPaperRevision(SqliteResearchEventStoreAdapter(schema.connect)),
        ),
    )


def test_equivalent_observations_reuse_revision_and_event(tmp_path: Path) -> None:
    schema, project = _projector(tmp_path / "runtime")

    first = project(_replay("observation:" + "a" * 64, 1))
    second = project(_replay("observation:" + "c" * 64, 1))

    assert first.revision_id == second.revision_id
    assert first.event_id == second.event_id
    assert first.created_revision is True
    assert second.created_revision is False
    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM paper_revisions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM research_events"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM catalog_field_provenance"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_new_native_version_creates_new_revision_even_with_same_text(
    tmp_path: Path,
) -> None:
    schema, project = _projector(tmp_path / "runtime")

    first = project(_replay("observation:" + "a" * 64, 1))
    second = project(
        replace(
            _replay("observation:" + "c" * 64, 1),
            record=_record(2),
        )
    )

    assert first.revision_id != second.revision_id
    assert first.event_id != second.event_id
    connection = schema.connect()
    try:
        versions = connection.execute(
            "SELECT native_version FROM paper_revisions ORDER BY native_version"
        ).fetchall()
        assert [row[0] for row in versions] == ["1", "2"]
        assert connection.execute(
            "SELECT count(*) FROM research_events"
        ).fetchone()[0] == 2
    finally:
        connection.close()
