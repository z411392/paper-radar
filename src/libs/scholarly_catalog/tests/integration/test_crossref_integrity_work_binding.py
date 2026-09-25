import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.discovery.dtos.crossref_harvest import CrossrefPendingItem
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_integrity_store_adapter import (
    SqliteCrossrefIntegrityStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_integrity_work_binding_store_adapter import (
    SqliteCrossrefIntegrityWorkBindingStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_provider_revision_store_adapter import (
    SqliteCrossrefProviderRevisionStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_relation_store_adapter import (
    SqliteCrossrefRelationStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_paper_identity_store_adapter import (
    SqlitePaperIdentityStoreAdapter,
)
from libs.scholarly_catalog.application.commands.bind_crossref_integrity_works import (
    BindCrossrefIntegrityWorks,
)
from libs.scholarly_catalog.application.commands.project_crossref_pending_item import (
    ProjectCrossrefPendingItem,
)
from libs.scholarly_catalog.application.commands.resolve_paper_identity import (
    ResolvePaperIdentity,
)
from libs.scholarly_catalog.application.queries.read_paper_identity import ReadPaperIdentity
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.dtos.paper_identity_observation import (
    PaperIdentityObservation,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)

NOW = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "integrity-binding.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in (
        "0001-object-registry.sql",
        "0003-scholarly-catalog.sql",
        "0017-crossref-provider-revisions.sql",
        "0019-crossref-relation-assertions.sql",
        "0020-crossref-integrity-assertions.sql",
        "0021-crossref-integrity-work-bindings.sql",
    ):
        connection.executescript(
            (root / "migrations" / name).read_text(encoding="utf-8")
        )
    connection.commit()
    connection.close()

    connect = _connect(path)
    normalize = NormalizePaperIdentifier()
    identity_store = SqlitePaperIdentityStoreAdapter(connect)
    resolve_identity = ResolvePaperIdentity(normalize, identity_store)
    bindings = BindCrossrefIntegrityWorks(
        ReadPaperIdentity(normalize, identity_store),
        SqliteCrossrefIntegrityWorkBindingStoreAdapter(connect),
    )
    project = ProjectCrossrefPendingItem(
        normalize,
        SqliteCrossrefProviderRevisionStoreAdapter(connect),
        SqliteCrossrefRelationStoreAdapter(connect),
        SqliteCrossrefIntegrityStoreAdapter(connect),
        bindings,
    )
    return path, identity_store, resolve_identity, project


def _identity(resolve, doi: str, *, key: str, kind: str = "publication"):
    return resolve(
        PaperIdentityObservation(
            "obs:" + key,
            "doi",
            doi,
            "Paper " + key,
            hashlib.sha256(("content:" + key).encode()).hexdigest(),
            kind,
            "https://doi.org/" + doi.lower(),
            "published",
            NOW,
        )
    )


def _item(value: dict, *, page="crossref-page:one", ordinal=0):
    canonical = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return CrossrefPendingItem(
        page,
        ordinal,
        value["DOI"],
        canonical,
        hashlib.sha256(canonical.encode("ascii")).hexdigest(),
    )


def _update(target="10.1000/TARGET"):
    return {
        "DOI": target,
        "type": "retraction",
        "source": "publisher",
        "label": "Retraction",
    }


def _rows(path: Path, query: str):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(query).fetchall()
    finally:
        connection.close()


def test_known_notice_and_target_are_bound_to_existing_catalog_identity(
    tmp_path: Path,
) -> None:
    path, _, resolve_identity, project = _setup(tmp_path)
    notice = _identity(
        resolve_identity,
        "10.1000/NOTICE",
        key="notice",
        kind="notice",
    )
    target = _identity(resolve_identity, "10.1000/TARGET", key="target")

    project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": [_update()],
            }
        ),
        observed_at=NOW,
    )

    rows = _rows(
        path,
        "SELECT role,canonical_doi,manifestation_id,work_id,canonical_work_id "
        "FROM crossref_integrity_work_bindings ORDER BY role",
    )
    assert [tuple(row) for row in rows] == [
        (
            "notice",
            "10.1000/notice",
            notice.manifestation_id,
            notice.work_id,
            notice.canonical_work_id,
        ),
        (
            "target",
            "10.1000/target",
            target.manifestation_id,
            target.work_id,
            target.canonical_work_id,
        ),
    ]


def test_missing_target_identity_remains_unresolved_without_creating_work(
    tmp_path: Path,
) -> None:
    path, _, resolve_identity, project = _setup(tmp_path)
    notice = _identity(
        resolve_identity,
        "10.1000/NOTICE",
        key="notice",
        kind="notice",
    )
    before = len(_rows(path, "SELECT * FROM paper_works"))

    result = project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": [_update("10.1000/UNKNOWN")],
            }
        ),
        observed_at=NOW,
    )

    assert result.canonical_doi == "10.1000/notice"
    assert len(_rows(path, "SELECT * FROM paper_works")) == before
    bindings = _rows(
        path,
        "SELECT role,work_id FROM crossref_integrity_work_bindings",
    )
    assert [tuple(row) for row in bindings] == [("notice", notice.work_id)]
    assert len(_rows(path, "SELECT * FROM crossref_integrity_assertions")) == 1


def test_identity_corruption_fails_closed_after_assertion_is_durable(
    tmp_path: Path,
) -> None:
    path, _, resolve_identity, project = _setup(tmp_path)
    _identity(resolve_identity, "10.1000/NOTICE", key="notice", kind="notice")
    target = _identity(resolve_identity, "10.1000/TARGET", key="target")
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "DELETE FROM paper_revisions WHERE manifestation_id=?",
            (target.manifestation_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        CrossrefProviderProjectionError,
        match="crossref_integrity_identity_lookup_failed",
    ):
        project(
            _item(
                {
                    "DOI": "10.1000/NOTICE",
                    "update-to": [_update()],
                }
            ),
            observed_at=NOW,
        )

    assert len(_rows(path, "SELECT * FROM crossref_integrity_assertions")) == 1
    roles = _rows(
        path,
        "SELECT role FROM crossref_integrity_work_bindings ORDER BY role",
    )
    assert [row["role"] for row in roles] == ["notice"]


def test_alias_change_does_not_rewrite_historical_binding(
    tmp_path: Path,
) -> None:
    path, identity_store, resolve_identity, project = _setup(tmp_path)
    _identity(resolve_identity, "10.1000/NOTICE", key="notice", kind="notice")
    target = _identity(resolve_identity, "10.1000/TARGET", key="target")
    canonical = _identity(resolve_identity, "10.1000/CANON", key="canon")
    item = _item(
        {
            "DOI": "10.1000/NOTICE",
            "update-to": [_update()],
        }
    )

    project(item, observed_at=NOW)
    original = _rows(
        path,
        "SELECT canonical_work_id FROM crossref_integrity_work_bindings "
        "WHERE role='target'",
    )[0]["canonical_work_id"]

    identity_store.merge_alias(
        target.work_id,
        canonical.work_id,
        '{"reason":"test-alias"}',
        NOW,
    )
    project(item, observed_at=NOW)

    rebound = _rows(
        path,
        "SELECT canonical_work_id FROM crossref_integrity_work_bindings "
        "WHERE role='target'",
    )[0]["canonical_work_id"]
    assert original == target.work_id
    assert rebound == original
