import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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

NOW = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "integrity.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in (
        "0017-crossref-provider-revisions.sql",
        "0019-crossref-relation-assertions.sql",
        "0020-crossref-integrity-assertions.sql",
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
        SqliteCrossrefIntegrityStoreAdapter(connect),
    )


def _canonical(value: dict) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _item(value: dict, *, page="crossref-page:one", ordinal=0):
    canonical = _canonical(value)
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


def _update(
    *,
    target="10.1000/TARGET",
    kind="retraction",
    source="publisher",
    record_id=None,
    updated=None,
    label="Retraction",
):
    value = {
        "DOI": target,
        "type": kind,
        "source": source,
        "label": label,
    }
    if record_id is not None:
        value["record-id"] = record_id
    if updated is not None:
        value["updated"] = updated
    return value


def test_publisher_and_retraction_watch_assertions_both_remain(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": [
                    _update(source="publisher"),
                    _update(source="retraction-watch", record_id="rw:123"),
                ],
            }
        ),
        observed_at=NOW,
    )
    rows = _rows(
        path,
        "SELECT source_raw,record_id_raw,event_class "
        "FROM crossref_integrity_assertions ORDER BY source_raw",
    )
    assert [tuple(row) for row in rows] == [
        ("publisher", None, "retraction"),
        ("retraction-watch", "rw:123", "retraction"),
    ]


def test_same_rw_record_id_conflicting_types_are_append_only(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": [
                    _update(
                        source="retraction-watch",
                        record_id="rw:123",
                        kind="retraction",
                    ),
                    _update(
                        source="retraction-watch",
                        record_id="rw:123",
                        kind="expression of concern",
                    ),
                ],
            }
        ),
        observed_at=NOW,
    )
    rows = _rows(
        path,
        "SELECT type_raw,event_class FROM crossref_integrity_assertions "
        "ORDER BY type_raw",
    )
    assert [tuple(row) for row in rows] == [
        ("expression of concern", "expression_of_concern"),
        ("retraction", "retraction"),
    ]


def test_notice_record_is_source_and_update_to_doi_is_target(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": [_update(target="10.1000/ORIGINAL", kind="correction")],
            }
        ),
        observed_at=NOW,
    )
    row = _rows(
        path,
        "SELECT source_notice_doi,target_doi_raw,target_canonical_doi,"
        "target_normalization_state,event_class "
        "FROM crossref_integrity_assertions",
    )[0]
    assert tuple(row) == (
        "10.1000/notice",
        "10.1000/ORIGINAL",
        "10.1000/original",
        "normalized",
        "correction",
    )


def test_unknown_type_and_source_are_preserved_without_reclassification(
    tmp_path: Path,
) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": [
                    _update(
                        kind="future-integrity-kind",
                        source="future-provider",
                    )
                ],
            }
        ),
        observed_at=NOW,
    )
    row = _rows(
        path,
        "SELECT type_raw,source_raw,event_class FROM crossref_integrity_assertions",
    )[0]
    assert tuple(row) == (
        "future-integrity-kind",
        "future-provider",
        "other_update",
    )


def test_invalid_and_missing_target_doi_are_durable(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    missing = _update()
    del missing["DOI"]
    project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": [
                    _update(target="not-a-doi", kind="retraction"),
                    missing,
                ],
            }
        ),
        observed_at=NOW,
    )
    rows = _rows(
        path,
        "SELECT target_doi_raw,target_canonical_doi,target_normalization_state "
        "FROM crossref_integrity_assertions ORDER BY update_ordinal",
    )
    assert [tuple(row) for row in rows] == [
        ("not-a-doi", None, "invalid"),
        (None, None, "missing"),
    ]


def test_partial_updated_date_is_parsed_and_invalid_date_keeps_raw_gap(
    tmp_path: Path,
) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": [
                    _update(
                        updated={"date-parts": [[2026]]},
                        kind="correction",
                    ),
                    _update(
                        updated={"date-parts": [[2026, 13, 40]]},
                        kind="correction",
                        label="Bad date",
                    ),
                ],
            }
        ),
        observed_at=NOW,
    )
    rows = _rows(
        path,
        "SELECT updated_value,updated_precision,updated_raw_json "
        "FROM crossref_integrity_assertions ORDER BY update_ordinal",
    )
    gaps = _rows(
        path,
        "SELECT path,error_code FROM crossref_integrity_parse_gaps "
        "ORDER BY path",
    )
    assert rows[0]["updated_value"] == "2026"
    assert rows[0]["updated_precision"] == "year"
    assert rows[1]["updated_value"] is None
    assert rows[1]["updated_precision"] is None
    assert rows[1]["updated_raw_json"] == '{"date-parts":[[2026,13,40]]}'
    assert [tuple(row) for row in gaps] == [
        ("update-to[1].updated", "integrity_updated_invalid"),
    ]


def test_same_assertion_across_provider_revisions_reuses_assertion(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    update = [_update(source="publisher", record_id="notice:1")]
    one = project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": update,
                "reference-count": 1,
            }
        ),
        observed_at=NOW,
    )
    two = project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": update,
                "reference-count": 2,
            },
            page="crossref-page:two",
        ),
        observed_at=NOW,
    )
    assert one.provider_revision_id != two.provider_revision_id
    assert len(_rows(path, "SELECT * FROM crossref_integrity_assertions")) == 1
    assert len(
        _rows(path, "SELECT * FROM crossref_integrity_revision_observations")
    ) == 2


def test_same_day_multiple_notices_do_not_collide(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    updated = {"date-parts": [[2026, 9, 25]]}
    for notice, record, page in (
        ("10.1000/NOTICE-A", "a", "crossref-page:one"),
        ("10.1000/NOTICE-B", "b", "crossref-page:two"),
    ):
        project(
            _item(
                {
                    "DOI": notice,
                    "update-to": [
                        _update(
                            target="10.1000/TARGET",
                            updated=updated,
                            record_id=record,
                        )
                    ],
                },
                page=page,
            ),
            observed_at=NOW,
        )
    rows = _rows(
        path,
        "SELECT source_notice_doi,record_id_raw,id "
        "FROM crossref_integrity_assertions ORDER BY source_notice_doi",
    )
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]


def test_integrity_projection_does_not_create_synthetic_relation(tmp_path: Path) -> None:
    path, project = _setup(tmp_path)
    project(
        _item(
            {
                "DOI": "10.1000/NOTICE",
                "update-to": [_update(kind="retraction")],
            }
        ),
        observed_at=NOW,
    )
    assert _rows(path, "SELECT * FROM crossref_relation_assertions") == []
