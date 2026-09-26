from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from libs.discovery.dtos.pubmed_bibliography_record import PubmedBibliographyRecord
from libs.discovery.dtos.pubmed_observation_replay import PubmedObservationReplay
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
from libs.scholarly_catalog.application.commands.project_pubmed_observation import (
    ProjectPubmedObservation,
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


def _replay(observation_id: str) -> PubmedObservationReplay:
    return PubmedObservationReplay(
        observation_id=observation_id,
        unit_id="unit:pubmed",
        payload_object_id="raw:" + "b" * 64,
        parser_version="pubmed-eutils-parser-v1",
        observed_at=NOW,
        record=_record(),
    )


def _projector(root: Path):
    migrations = load_workspace_migrations(with_runtime=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=3)
    identity = SqlitePaperIdentityStoreAdapter(schema.connect)
    return (
        schema,
        ProjectPubmedObservation(
            ResolvePaperIdentity(NormalizePaperIdentifier(), identity),
            RecordPaperRevision(SqliteResearchEventStoreAdapter(schema.connect)),
        ),
    )


def test_equivalent_pubmed_observations_reuse_revision_and_event(
    tmp_path: Path,
) -> None:
    schema, project = _projector(tmp_path / "runtime")

    first = project(_replay("observation:" + "a" * 64))
    second = project(_replay("observation:" + "c" * 64))

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
        work = connection.execute(
            "SELECT first_public_date,first_public_precision FROM paper_works"
        ).fetchone()
        assert tuple(work) == (None, None)
    finally:
        connection.close()


def test_pubmed_bibliographic_change_creates_new_revision_event(
    tmp_path: Path,
) -> None:
    schema, project = _projector(tmp_path / "runtime")

    first = project(_replay("observation:" + "a" * 64))
    changed = replace(
        _replay("observation:" + "c" * 64),
        record=replace(_record(), journal_title="Other Journal"),
    )
    second = project(changed)

    assert first.revision_id != second.revision_id
    assert first.event_id != second.event_id
    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM paper_revisions"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM research_events"
        ).fetchone()[0] == 2
    finally:
        connection.close()
