from datetime import datetime, timezone
from pathlib import Path

import pytest

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
from libs.scholarly_catalog.application.commands.resolve_paper_identity import (
    ResolvePaperIdentity,
)
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.dtos.paper_identity_observation import (
    PaperIdentityObservation,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


NOW = datetime(2026, 9, 26, 13, 20, tzinfo=timezone.utc)


def _observation(pmid: str, marker: str) -> PaperIdentityObservation:
    return PaperIdentityObservation(
        source_observation_id="observation:" + marker * 64,
        identifier_namespace="pmid",
        identifier_value=pmid,
        title="Paper " + pmid,
        content_fingerprint=marker * 64,
        manifestation_kind="publication",
        landing_url="https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
        publication_status="published",
        observed_at=NOW,
    )


def _tools(root: Path):
    migrations = load_workspace_migrations(with_runtime=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=3)
    store = SqlitePaperIdentityStoreAdapter(schema.connect)
    resolve = ResolvePaperIdentity(NormalizePaperIdentifier(), store)
    return schema, store, resolve


def _bind(store: SqlitePaperIdentityStoreAdapter):
    return getattr(store, "bind_identifier")


def test_secondary_doi_binding_is_idempotent_without_new_catalog_entities(
    tmp_path: Path,
) -> None:
    schema, store, resolve = _tools(tmp_path / "runtime")
    paper = resolve(_observation("100", "a"))
    doi = NormalizePaperIdentifier()("doi", "10.1234/Example")
    bind = _bind(store)

    bind(paper.manifestation_id, doi, "evidence:crosswalk:100")
    bind(paper.manifestation_id, doi, "evidence:crosswalk:100")

    connection = schema.connect()
    try:
        counts = tuple(
            connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in (
                "paper_works",
                "paper_manifestations",
                "paper_revisions",
                "external_identifiers",
            )
        )
        assert counts == (1, 1, 1, 2)
        row = connection.execute(
            "SELECT manifestation_id,source_evidence_id "
            "FROM external_identifiers WHERE namespace='doi' "
            "AND normalized_value='10.1234/example'"
        ).fetchone()
        assert tuple(row) == (
            paper.manifestation_id,
            "evidence:crosswalk:100",
        )
    finally:
        connection.close()


def test_secondary_identifier_cannot_move_between_manifestations(
    tmp_path: Path,
) -> None:
    schema, store, resolve = _tools(tmp_path / "runtime")
    first = resolve(_observation("100", "a"))
    second = resolve(_observation("200", "b"))
    doi = NormalizePaperIdentifier()("doi", "10.1234/example")
    bind = _bind(store)
    bind(first.manifestation_id, doi, "evidence:first")

    with pytest.raises(PaperIdentityError, match="identifier_binding_conflict"):
        bind(second.manifestation_id, doi, "evidence:second")

    connection = schema.connect()
    try:
        row = connection.execute(
            "SELECT manifestation_id,source_evidence_id "
            "FROM external_identifiers WHERE namespace='doi' "
            "AND normalized_value='10.1234/example'"
        ).fetchone()
        assert tuple(row) == (first.manifestation_id, "evidence:first")
    finally:
        connection.close()


def test_secondary_identifier_replay_requires_same_evidence(
    tmp_path: Path,
) -> None:
    _, store, resolve = _tools(tmp_path / "runtime")
    paper = resolve(_observation("100", "a"))
    doi = NormalizePaperIdentifier()("doi", "10.1234/example")
    bind = _bind(store)
    bind(paper.manifestation_id, doi, "evidence:first")

    with pytest.raises(PaperIdentityError, match="identifier_binding_conflict"):
        bind(paper.manifestation_id, doi, "evidence:changed")


def test_secondary_identifier_requires_manifestation_level_identity(
    tmp_path: Path,
) -> None:
    schema, store, resolve = _tools(tmp_path / "runtime")
    paper = resolve(_observation("100", "a"))
    versioned = NormalizePaperIdentifier()("arxiv", "2501.12345v2")
    doi = NormalizePaperIdentifier()("doi", "10.1234/example")
    bind = _bind(store)

    with pytest.raises(
        PaperIdentityError,
        match="versioned_identifier_not_manifestation_identity",
    ):
        bind(paper.manifestation_id, versioned, "evidence:versioned")
    with pytest.raises(PaperIdentityError, match="manifestation_missing"):
        bind("manifestation:missing", doi, "evidence:missing")

    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM external_identifiers"
        ).fetchone()[0] == 1
    finally:
        connection.close()
