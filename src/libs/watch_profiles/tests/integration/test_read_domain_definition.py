import json
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.dtos.migration import Migration
from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter import (
    SqliteWatchProfileStoreAdapter,
)
from libs.watch_profiles.application.commands.import_domain_seeds import ImportDomainSeeds
from libs.watch_profiles.application.queries.read_domain_definition import ReadDomainDefinition
from libs.watch_profiles.domain.services.normalize_watch_configuration import NormalizeWatchConfiguration
from libs.watch_profiles.exceptions.watch_configuration_error import WatchConfigurationError

ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture
def store(tmp_path):
    migrations = tuple(
        Migration(index, path.name, path.read_text())
        for index, path in enumerate(sorted((ROOT / "migrations").glob("*.sql"))[:2], 1)
    )
    root = tmp_path / "workspace"
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    factory = SqliteConnectionFactory(root)
    return SqliteWatchProfileStoreAdapter(factory.connect), factory


def payload() -> str:
    return json.dumps(
        {
            "domains": [
                {
                    "id": "statistics",
                    "name": "統計學",
                    "aliases": ["statistics", "統計學"],
                    "include": ["statistics"],
                    "exclude": [],
                    "sources": ["arxiv", "crossref"],
                    "source_categories": {"arxiv": ["stat.*"]},
                }
            ]
        },
        ensure_ascii=False,
    )


def test_read_exact_published_domain_revision(store):
    adapter, _ = store
    ImportDomainSeeds(NormalizeWatchConfiguration(frozenset({"arxiv", "crossref"})), adapter)(payload())
    domain = ReadDomainDefinition(adapter)("statistics", 1)
    assert domain.domain_id == "statistics"
    assert domain.revision == 1
    assert domain.sources == ("arxiv", "crossref")
    assert domain.source_categories == (("arxiv", ("stat.*",)),)
    assert domain.aliases == ("statistics", "統計學")


def test_corrupt_duplicate_key_definition_is_rejected(store):
    adapter, factory = store
    ImportDomainSeeds(
        NormalizeWatchConfiguration(frozenset({"arxiv", "crossref"})), adapter
    )(payload())
    connection = factory.connect()
    try:
        original = connection.execute(
            "SELECT definition_json FROM domain_definitions WHERE id='statistics' AND revision=1"
        ).fetchone()[0]
        corrupt = original.replace('"id":"statistics"', '"id":"statistics","id":"statistics"', 1)
        connection.execute(
            "UPDATE domain_definitions SET definition_json=? WHERE id='statistics' AND revision=1",
            (corrupt,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(WatchConfigurationError, match="invalid_domain_definition"):
        ReadDomainDefinition(adapter)("statistics", 1)
