import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from libs.discovery.dtos.crossref_harvest import CrossrefPendingItem
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


NOW = datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "relations.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in (
        "0018-crossref-provider-revisions.sql",
        "0020-crossref-relation-assertions.sql",
    ):
        connection.executescript(
            (root / "migrations" / name).read_text(encoding="utf-8")
        )
    connection.commit()
    connection.close()
    connect = _connect(path)
    return path, ProjectCrossrefPendingItem(
        NormalizePaperIdentifier(),
        SqliteCrossrefProviderRevisionStoreAdapter(connect),
        SqliteCrossrefRelationStoreAdapter(connect),
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


def _rows(path: Path, query: str):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(query).fetchall()
    finally:
        connection.close()


def test_subject_relation_is_stored_without_synthetic_reciprocal(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/PREPRINT",
                "relation": {
                    "is-preprint-of": [
                        {
                            "id-type": "doi",
                            "id": "10.1000/ARTICLE",
                            "asserted-by": "subject",
                        }
                    ]
                },
            }
        ),
        observed_at=NOW,
    )

    assertions = _rows(
        path,
        "SELECT predicate_raw,target_id_type_raw,target_value_raw,"
        "asserted_by_raw,relation_class,target_namespace,"
        "target_normalized_value,target_normalization_state "
        "FROM crossref_relation_assertions",
    )

    assert [tuple(row) for row in assertions] == [
        (
            "is-preprint-of",
            "doi",
            "10.1000/ARTICLE",
            "subject",
            "intra_work",
            "doi",
            "10.1000/article",
            "normalized",
        )
    ]
    assert _rows(
        path,
        "SELECT 1 FROM crossref_relation_assertions "
        "WHERE predicate_raw='has-preprint'",
    ) == []


def test_object_asserted_reciprocal_preserves_provider_provenance(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/ARTICLE",
                "relation": {
                    "has-preprint": [
                        {
                            "id-type": "doi",
                            "id": "10.1000/PREPRINT",
                            "asserted-by": "object",
                        }
                    ]
                },
            }
        ),
        observed_at=NOW,
    )

    row = _rows(
        path,
        "SELECT asserted_by_raw FROM crossref_relation_assertions",
    )[0]

    assert row["asserted_by_raw"] == "object"


def test_unknown_predicate_and_identifier_type_are_preserved_raw(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/A",
                "relation": {
                    "future-relation": [
                        {
                            "id-type": "future-id",
                            "id": "opaque:ABC",
                            "asserted-by": "future-origin",
                        }
                    ]
                },
            }
        ),
        observed_at=NOW,
    )

    row = _rows(path, "SELECT * FROM crossref_relation_assertions")[0]

    assert row["predicate_raw"] == "future-relation"
    assert row["target_id_type_raw"] == "future-id"
    assert row["target_value_raw"] == "opaque:ABC"
    assert row["asserted_by_raw"] == "future-origin"
    assert row["relation_class"] == "unknown"
    assert row["target_namespace"] is None
    assert row["target_normalized_value"] is None
    assert row["target_normalization_state"] == "raw"


def test_pmcid_target_normalizes_through_catalog_owner(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/A",
                "relation": {
                    "references": [
                        {
                            "id-type": "pmcid",
                            "id": "pmc0012345",
                            "asserted-by": "subject",
                        }
                    ]
                },
            }
        ),
        observed_at=NOW,
    )

    row = _rows(
        path,
        "SELECT target_namespace,target_normalized_value,"
        "target_normalization_state FROM crossref_relation_assertions",
    )[0]

    assert tuple(row) == ("pmc", "PMC12345", "normalized")


def test_invalid_target_identifier_is_durable_not_whole_item_failure(
    tmp_path: Path,
) -> None:
    path, project = _setup(tmp_path)
    result = project(
        _item(
            {
                "DOI": "10.1000/A",
                "title": ["Paper"],
                "relation": {
                    "references": [
                        {
                            "id-type": "doi",
                            "id": "not-a-doi",
                            "asserted-by": "subject",
                        }
                    ]
                },
            }
        ),
        observed_at=NOW,
    )

    assert result.canonical_doi == "10.1000/a"
    row = _rows(
        path,
        "SELECT target_value_raw,target_normalized_value,"
        "target_normalization_state FROM crossref_relation_assertions",
    )[0]
    assert tuple(row) == ("not-a-doi", None, "invalid")


def test_same_edge_is_observed_across_provider_revisions(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    relation = {
        "is-version-of": [
            {
                "id-type": "doi",
                "id": "10.1000/BASE",
                "asserted-by": "subject",
            }
        ]
    }
    first = {"DOI": "10.1000/A", "relation": relation, "reference-count": 1}
    second = {"DOI": "10.1000/A", "relation": relation, "reference-count": 2}

    one = project(_item(first), observed_at=NOW)
    two = project(
        _item(second, page="crossref-page:two"),
        observed_at=NOW,
    )

    assert one.provider_revision_id != two.provider_revision_id
    assert len(_rows(path, "SELECT * FROM crossref_relation_assertions")) == 1
    assert (
        len(_rows(path, "SELECT * FROM crossref_relation_revision_observations"))
        == 2
    )


def test_duplicate_relation_entries_preserve_revision_ordinals(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    edge = {
        "id-type": "doi",
        "id": "10.1000/B",
        "asserted-by": "subject",
    }
    project(
        _item(
            {
                "DOI": "10.1000/A",
                "relation": {"references": [edge, edge]},
            }
        ),
        observed_at=NOW,
    )

    assert len(_rows(path, "SELECT * FROM crossref_relation_assertions")) == 1
    ordinals = _rows(
        path,
        "SELECT ordinal FROM crossref_relation_revision_observations "
        "ORDER BY ordinal",
    )
    assert [row["ordinal"] for row in ordinals] == [0, 1]


def test_malformed_relation_entry_becomes_parse_gap_not_item_loss(
    tmp_path: Path,
) -> None:
    path, project = _setup(tmp_path)
    result = project(
        _item(
            {
                "DOI": "10.1000/A",
                "relation": {
                    "references": [
                        {"id-type": "doi", "asserted-by": "subject"},
                        "not-an-object",
                    ]
                },
            }
        ),
        observed_at=NOW,
    )

    assert result.canonical_doi == "10.1000/a"
    gaps = _rows(
        path,
        "SELECT path,error_code FROM crossref_relation_parse_gaps "
        "ORDER BY path",
    )
    assert [tuple(row) for row in gaps] == [
        ("relation.references[0]", "relation_target_missing"),
        ("relation.references[1]", "relation_entry_not_object"),
    ]


def _lifecycle_projector(
    path: Path,
    *,
    snapshot_complete: bool,
):
    connect = _connect(path)
    relation_store = SqliteCrossrefRelationStoreAdapter(connect)
    return (
        ProjectCrossrefPendingItem(
            NormalizePaperIdentifier(),
            SqliteCrossrefProviderRevisionStoreAdapter(connect),
            relation_store,
            relation_snapshot_complete=snapshot_complete,
        ),
        relation_store,
    )


def test_complete_snapshot_absence_marks_relation_no_longer_observed(
    tmp_path: Path,
) -> None:
    path, _ = _setup(tmp_path)
    complete, relations = _lifecycle_projector(
        path,
        snapshot_complete=True,
    )
    first = complete(
        _item(
            {
                "DOI": "10.1000/A",
                "reference-count": 1,
                "relation": {
                    "references": [
                        {
                            "id-type": "doi",
                            "id": "10.1000/B",
                            "asserted-by": "subject",
                        }
                    ]
                },
            }
        ),
        observed_at=NOW,
    )
    second = complete(
        _item(
            {
                "DOI": "10.1000/A",
                "reference-count": 2,
            },
            page="crossref-page:two",
        ),
        observed_at=NOW,
    )

    lifecycle = relations.lifecycle("10.1000/a")

    assert len(lifecycle) == 1
    assert lifecycle[0].state == "no_longer_observed"
    assert lifecycle[0].last_observed_provider_revision_id == (
        first.provider_revision_id
    )
    assert lifecycle[0].no_longer_observed_provider_revision_id == (
        second.provider_revision_id
    )


def test_partial_snapshot_absence_does_not_withdraw_relation(
    tmp_path: Path,
) -> None:
    path, _ = _setup(tmp_path)
    complete, relations = _lifecycle_projector(
        path,
        snapshot_complete=True,
    )
    partial, _ = _lifecycle_projector(
        path,
        snapshot_complete=False,
    )
    first = complete(
        _item(
            {
                "DOI": "10.1000/A",
                "reference-count": 1,
                "relation": {
                    "references": [
                        {
                            "id-type": "doi",
                            "id": "10.1000/B",
                            "asserted-by": "subject",
                        }
                    ]
                },
            }
        ),
        observed_at=NOW,
    )
    second = partial(
        _item(
            {
                "DOI": "10.1000/A",
                "reference-count": 2,
            },
            page="crossref-page:two",
        ),
        observed_at=NOW,
    )

    lifecycle = relations.lifecycle("10.1000/a")

    assert len(lifecycle) == 1
    assert lifecycle[0].state == "observed"
    assert lifecycle[0].last_observed_provider_revision_id == (
        first.provider_revision_id
    )
    assert lifecycle[0].no_longer_observed_provider_revision_id is None
    gaps = _rows(
        path,
        "SELECT error_code FROM crossref_relation_parse_gaps "
        "WHERE provider_revision_id='"
        + second.provider_revision_id
        + "'",
    )
    assert [row["error_code"] for row in gaps] == [
        "relation_snapshot_incomplete"
    ]
