import json
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
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_integrity_event_source_adapter import (
    SqliteCrossrefIntegrityEventSourceAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_research_event_store_adapter import (
    SqliteResearchEventStoreAdapter,
)
from libs.scholarly_catalog.application.commands.promote_crossref_integrity_events import (
    PromoteCrossrefIntegrityEvents,
)
from libs.scholarly_catalog.application.commands.record_paper_revision import (
    RecordPaperRevision,
)
from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityAssertionRef,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


NOW = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
TARGET_DOI = "10.1000/target"
NOTICE_DOI = "10.1000/notice"
WORK = "work:target"
MANIFESTATION = "manifestation:target"


def _setup(tmp_path: Path):
    root = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == 22
    factory = SqliteSchemaConnectionFactory(root, migrations, minimum_version=22)
    return factory


def _seed_identity(connect) -> None:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                WORK,
                "Target paper",
                "published",
                "2025",
                "year",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
            (
                MANIFESTATION,
                WORK,
                "crossref",
                TARGET_DOI,
                "publication",
                "https://doi.org/" + TARGET_DOI,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO external_identifiers VALUES(?,?,?,?)",
            ("doi", TARGET_DOI, MANIFESTATION, "fixture:identity"),
        )
        connection.execute(
            "INSERT INTO crossref_source_records VALUES(?,?,?)",
            (NOTICE_DOI, NOTICE_DOI, NOW.isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


def _seed_assertion(
    connect,
    *,
    assertion_id: str,
    event_class: str,
    type_raw: str,
    updated_value: str | None,
    updated_precision: str | None,
    bind_target: bool = True,
) -> CrossrefIntegrityAssertionRef:
    raw = json.dumps(
        {
            "DOI": TARGET_DOI,
            "type": type_raw,
            "source": "publisher",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    updated_raw = (
        None
        if updated_value is None
        else json.dumps(
            {"date-time": updated_value}
            if updated_precision == "second"
            else {"date-parts": [[2026, 9, 24]]},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO crossref_integrity_assertions VALUES("
            "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                assertion_id,
                NOTICE_DOI,
                "update_to",
                TARGET_DOI,
                TARGET_DOI,
                "normalized",
                NOTICE_DOI,
                TARGET_DOI,
                type_raw,
                "publisher",
                None,
                None,
                event_class,
                updated_value,
                updated_precision,
                updated_raw,
                raw,
                NOW.isoformat(),
            ),
        )
        if bind_target:
            connection.execute(
                "INSERT INTO crossref_integrity_work_bindings VALUES(?,?,?,?,?,?,?)",
                (
                    assertion_id,
                    "target",
                    TARGET_DOI,
                    MANIFESTATION,
                    WORK,
                    WORK,
                    NOW.isoformat(),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return CrossrefIntegrityAssertionRef(
        assertion_id,
        NOTICE_DOI,
        TARGET_DOI,
    )


def _command(connect):
    return PromoteCrossrefIntegrityEvents(
        SqliteCrossrefIntegrityEventSourceAdapter(connect),
        RecordPaperRevision(
            SqliteResearchEventStoreAdapter(connect)
        ),
    )


@pytest.mark.parametrize(
    "event_class,type_raw",
    [
        ("correction", "correction"),
        ("retraction", "retraction"),
    ],
)
def test_bound_target_promotes_supported_typed_event(
    tmp_path: Path,
    event_class: str,
    type_raw: str,
) -> None:
    factory = _setup(tmp_path)
    _seed_identity(factory.connect)
    assertion = _seed_assertion(
        factory.connect,
        assertion_id="assertion:" + event_class,
        event_class=event_class,
        type_raw=type_raw,
        updated_value="2026-09-24T12:34:56+00:00",
        updated_precision="second",
    )

    first = _command(factory.connect)((assertion,))
    second = _command(factory.connect)((assertion,))

    assert first == second
    assert len(first) == 1
    connection = factory.connect()
    try:
        row = connection.execute(
            "SELECT * FROM research_events"
        ).fetchone()
        work = connection.execute(
            "SELECT publication_status FROM paper_works WHERE id=?",
            (WORK,),
        ).fetchone()
    finally:
        connection.close()
    assert row["work_id"] == WORK
    assert row["event_kind"] == event_class
    assert row["revision_id"] is None
    assert row["occurred_at"] == "2026-09-24T12:34:56+00:00"
    evidence = json.loads(row["source_evidence_json"])
    assert evidence["source_evidence_id"] == assertion.assertion_id
    assert evidence["source_evidence"]["provider_attributed"] is True
    assert evidence["source_evidence"]["target_canonical_doi"] == TARGET_DOI
    assert work["publication_status"] == "published"


def test_unresolved_target_does_not_create_event_or_work(tmp_path: Path) -> None:
    factory = _setup(tmp_path)
    _seed_identity(factory.connect)
    assertion = _seed_assertion(
        factory.connect,
        assertion_id="assertion:unresolved",
        event_class="retraction",
        type_raw="retraction",
        updated_value=None,
        updated_precision=None,
        bind_target=False,
    )

    result = _command(factory.connect)((assertion,))

    assert result == ()
    connection = factory.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM research_events"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM paper_works"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    "event_class",
    ["expression_of_concern", "reinstatement", "other_update"],
)
def test_unsupported_integrity_class_is_not_lossily_mapped(
    tmp_path: Path,
    event_class: str,
) -> None:
    factory = _setup(tmp_path)
    _seed_identity(factory.connect)
    assertion = _seed_assertion(
        factory.connect,
        assertion_id="assertion:" + event_class,
        event_class=event_class,
        type_raw=event_class,
        updated_value=None,
        updated_precision=None,
    )

    assert _command(factory.connect)((assertion,)) == ()
    connection = factory.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM research_events"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_day_precision_does_not_invent_midnight_occurred_at(tmp_path: Path) -> None:
    factory = _setup(tmp_path)
    _seed_identity(factory.connect)
    assertion = _seed_assertion(
        factory.connect,
        assertion_id="assertion:day",
        event_class="correction",
        type_raw="correction",
        updated_value="2026-09-24",
        updated_precision="day",
    )

    _command(factory.connect)((assertion,))

    connection = factory.connect()
    try:
        occurred = connection.execute(
            "SELECT occurred_at FROM research_events"
        ).fetchone()[0]
    finally:
        connection.close()
    assert occurred is None


def test_corrupt_target_binding_fails_closed(tmp_path: Path) -> None:
    factory = _setup(tmp_path)
    _seed_identity(factory.connect)
    assertion = _seed_assertion(
        factory.connect,
        assertion_id="assertion:corrupt",
        event_class="retraction",
        type_raw="retraction",
        updated_value=None,
        updated_precision=None,
    )
    connection = factory.connect()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE crossref_integrity_work_bindings "
            "SET canonical_doi='10.1000/wrong' "
            "WHERE assertion_id=? AND role='target'",
            (assertion.assertion_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        CrossrefProviderProjectionError,
        match="crossref_integrity_event_binding_corrupt",
    ):
        _command(factory.connect)((assertion,))
