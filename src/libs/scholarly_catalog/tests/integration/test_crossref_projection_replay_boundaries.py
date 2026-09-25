import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.discovery.dtos.crossref_harvest import CrossrefPendingItem
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_integrity_store_adapter import (
    SqliteCrossrefIntegrityStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_provider_revision_store_adapter import (
    SqliteCrossrefProviderRevisionStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_relation_store_adapter import (
    SqliteCrossrefRelationStoreAdapter,
)
from libs.scholarly_catalog.application.commands.project_crossref_pending_item import (
    ProjectCrossrefPendingItem,
)
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


NOW = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "projection-replay.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in (
        "0018-crossref-provider-revisions.sql",
        "0020-crossref-relation-assertions.sql",
        "0021-crossref-integrity-assertions.sql",
    ):
        connection.executescript(
            (root / "migrations" / name).read_text(encoding="utf-8")
        )
    connection.commit()
    connection.close()

    connect = _connect(path)
    provider = SqliteCrossrefProviderRevisionStoreAdapter(connect)
    relation = SqliteCrossrefRelationStoreAdapter(connect)
    integrity = SqliteCrossrefIntegrityStoreAdapter(connect)
    return path, provider, relation, integrity


def _item():
    value = {
        "DOI": "10.1000/NOTICE",
        "relation": {
            "references": [
                {
                    "id-type": "doi",
                    "id": "10.1000/TARGET",
                    "asserted-by": "subject",
                }
            ]
        },
        "update-to": [
            {
                "DOI": "10.1000/TARGET",
                "type": "correction",
                "source": "publisher",
            }
        ],
    }
    canonical = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return CrossrefPendingItem(
        "crossref-page:replay",
        0,
        value["DOI"],
        canonical,
        hashlib.sha256(canonical.encode("ascii")).hexdigest(),
    )


def _count(path: Path, table: str) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


class FailAfterRelationCommit:
    def __init__(self, delegate):
        self.delegate = delegate
        self.failed = False

    def register(self, assertions, gaps):
        self.delegate.register(assertions, gaps)
        if not self.failed:
            self.failed = True
            raise CrossrefProviderProjectionError(
                "injected_relation_after_commit"
            )


class FailAfterIntegrityCommit:
    def __init__(self, delegate):
        self.delegate = delegate
        self.failed = False

    def register(self, assertions, gaps):
        result = self.delegate.register(assertions, gaps)
        if not self.failed:
            self.failed = True
            raise CrossrefProviderProjectionError(
                "injected_integrity_after_commit"
            )
        return result


def test_relation_commit_then_failure_replays_without_duplicate_provider_state(
    tmp_path: Path,
) -> None:
    path, provider, relation, integrity = _setup(tmp_path)
    first = ProjectCrossrefPendingItem(
        NormalizePaperIdentifier(),
        provider,
        FailAfterRelationCommit(relation),
        integrity,
    )

    with pytest.raises(
        CrossrefProviderProjectionError,
        match="injected_relation_after_commit",
    ):
        first(_item(), observed_at=NOW)

    assert _count(path, "crossref_provider_revisions") == 1
    assert _count(path, "crossref_provider_observations") == 1
    assert _count(path, "crossref_relation_assertions") == 1
    assert _count(path, "crossref_relation_revision_observations") == 1
    assert _count(path, "crossref_integrity_assertions") == 0

    replay = ProjectCrossrefPendingItem(
        NormalizePaperIdentifier(),
        provider,
        relation,
        integrity,
    )
    result = replay(_item(), observed_at=NOW + timedelta(hours=1))

    assert result.replayed is True
    assert _count(path, "crossref_provider_revisions") == 1
    assert _count(path, "crossref_provider_observations") == 1
    assert _count(path, "crossref_relation_assertions") == 1
    assert _count(path, "crossref_relation_revision_observations") == 1
    assert _count(path, "crossref_integrity_assertions") == 1
    assert _count(path, "crossref_integrity_revision_observations") == 1


def test_integrity_commit_then_failure_replays_without_duplicate_assertions(
    tmp_path: Path,
) -> None:
    path, provider, relation, integrity = _setup(tmp_path)
    first = ProjectCrossrefPendingItem(
        NormalizePaperIdentifier(),
        provider,
        relation,
        FailAfterIntegrityCommit(integrity),
    )

    with pytest.raises(
        CrossrefProviderProjectionError,
        match="injected_integrity_after_commit",
    ):
        first(_item(), observed_at=NOW)

    assert _count(path, "crossref_provider_revisions") == 1
    assert _count(path, "crossref_relation_assertions") == 1
    assert _count(path, "crossref_integrity_assertions") == 1
    assert _count(path, "crossref_integrity_revision_observations") == 1

    replay = ProjectCrossrefPendingItem(
        NormalizePaperIdentifier(),
        provider,
        relation,
        integrity,
    )
    result = replay(_item(), observed_at=NOW + timedelta(hours=1))

    assert result.replayed is True
    assert _count(path, "crossref_provider_revisions") == 1
    assert _count(path, "crossref_provider_observations") == 1
    assert _count(path, "crossref_relation_assertions") == 1
    assert _count(path, "crossref_relation_revision_observations") == 1
    assert _count(path, "crossref_integrity_assertions") == 1
    assert _count(path, "crossref_integrity_revision_observations") == 1
