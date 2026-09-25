import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.sqlite_crossref_harvest_journal_adapter import (
    SqliteCrossrefHarvestJournalAdapter,
)
from libs.discovery.dtos.crossref_capture import CrossrefReplayedPage
from libs.discovery.dtos.crossref_page import CrossrefDecodedItem, CrossrefDecodedPage, CrossrefWindowInput
from libs.discovery.exceptions.crossref_harvest_journal_error import CrossrefHarvestJournalError


NOW = datetime(2026, 9, 24, 2, 0, tzinfo=timezone.utc)
START = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "crossref.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript((root / "migrations/0001-object-registry.sql").read_text(encoding="utf-8"))
    connection.executescript((root / "migrations/0011-crossref-harvest.sql").read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO workspace_metadata VALUES(1,'workspace:test',1,0,?,NULL)",
        (NOW.isoformat(),),
    )
    connection.commit()
    connection.close()
    return path, SqliteCrossrefHarvestJournalAdapter(_connect(path))


def _plan(*, contact="fixture@example.invalid"):
    source = CrossrefSourceAdapter()
    plan = source.compile(
        CrossrefWindowInput(
            binding_key="personal:3:statistics:1:crossref",
            scope_query="statistical learning",
            from_index=START,
            until_index=END,
            contact_email=contact,
            config_version="profile-query-v1",
            rows=2,
        )
    )
    return source, plan


def _register_raw(path: Path, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    object_id = "raw:" + digest
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT OR IGNORE INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
        (
            object_id,
            digest,
            f"objects/raw/{digest[:2]}/{digest}",
            "raw",
            "application/octet-stream",
            len(content),
            "available",
            NOW.isoformat(),
            "source-response",
        ),
    )
    connection.commit()
    connection.close()
    return object_id


def _decoded(
    request,
    *,
    cursor_out="cursor:next",
    total=3,
    items=(),
    end=False,
    receipt_id="raw:" + "1" * 64,
):
    page = CrossrefDecodedPage(
        parser_version="crossref-rest-page-v1",
        query_fingerprint=request.query_fingerprint,
        parameters_fingerprint=request.parameters_fingerprint,
        request_fingerprint=request.request_fingerprint,
        response_sha256="2" * 64,
        cursor_in=request.cursor,
        next_cursor=cursor_out,
        reported_total=total,
        items=tuple(items),
        traversal_end_hint=end,
    )
    return CrossrefReplayedPage(
        receipt_id,
        "3" * 64,
        "2" * 64,
        page,
    )


def _item(ordinal: int, doi: str | None, *, quarantined=False):
    payload = {"DOI": doi} if doi is not None else {"title": ["missing DOI"]}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return CrossrefDecodedItem(
        ordinal,
        "quarantined" if quarantined else "decoded",
        doi,
        canonical,
        hashlib.sha256(canonical.encode("ascii")).hexdigest(),
        "crossref_item_doi_field_invalid" if quarantined else None,
    )


def _start(store, plan):
    window = store.ensure_window(plan, NOW)
    current = store.start_pass(plan, NOW)
    request = CrossrefSourceAdapter().page(plan, current.current_cursor)
    page = store.begin_page(current.pass_id, request, NOW)
    return window, current, request, page


def test_capture_receipt_is_discoverable_after_reopen_before_decode(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _, plan = _plan()
    _, current, request, page = _start(store, plan)
    receipt_id = _register_raw(path, b"receipt")

    store.record_attempt(
        page.page_id,
        receipt_id,
        action="accept",
        failure_code=None,
        recorded_at=NOW,
    )

    reopened = SqliteCrossrefHarvestJournalAdapter(_connect(path))
    resumed = reopened.resume_page(current.pass_id)

    assert resumed is not None
    assert resumed.page_id == page.page_id
    assert resumed.state == "captured"
    assert resumed.successful_receipt_id == receipt_id
    assert resumed.attempt_count == 1
    assert reopened.read_pass(current.pass_id).current_cursor == "*"


def test_retry_and_stop_receipts_never_advance_cursor_or_disappear(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _, plan = _plan()
    _, current, _, page = _start(store, plan)
    first = _register_raw(path, b"429 receipt")
    second = _register_raw(path, b"403 receipt")

    store.record_attempt(
        page.page_id,
        first,
        action="retry",
        failure_code="crossref_rate_limited",
        recorded_at=NOW,
    )
    store.record_attempt(
        page.page_id,
        second,
        action="stop",
        failure_code="crossref_forbidden",
        recorded_at=NOW + timedelta(seconds=1),
    )

    resumed = store.resume_page(current.pass_id)
    assert resumed is not None
    assert resumed.state == "requested"
    assert resumed.successful_receipt_id is None
    assert resumed.attempt_count == 2
    assert store.read_pass(current.pass_id).current_cursor == "*"

    connection = sqlite3.connect(path)
    receipts = connection.execute(
        "SELECT receipt_id,failure_code FROM crossref_harvest_page_attempts "
        "WHERE page_id=? ORDER BY attempt_no",
        (page.page_id,),
    ).fetchall()
    connection.close()
    assert receipts == [
        (first, "crossref_rate_limited"),
        (second, "crossref_forbidden"),
    ]


def test_decoded_page_cannot_commit_until_every_item_is_accounted(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _, plan = _plan()
    _, current, request, page = _start(store, plan)
    receipt_id = _register_raw(path, b"success receipt")
    store.record_attempt(page.page_id, receipt_id, action="accept", failure_code=None, recorded_at=NOW)

    replayed = _decoded(
        request,
        receipt_id=receipt_id,
        items=(
            _item(0, "10.1234/A"),
            _item(1, None, quarantined=True),
        ),
    )
    saved = store.save_decoded(page.page_id, replayed, NOW)

    assert saved.state == "decoded"
    pending = store.pending_items(page.page_id)
    assert [(item.ordinal, item.raw_doi) for item in pending] == [(0, "10.1234/A")]

    with pytest.raises(CrossrefHarvestJournalError, match="crossref_page_not_accounted"):
        store.commit_page(current.pass_id, page.page_id, NOW)

    store.mark_processed(
        page.page_id,
        0,
        canonical_doi="10.1234/a",
        outcome_ref="provider-revision:test",
        processed_at=NOW,
    )
    advanced = store.commit_page(current.pass_id, page.page_id, NOW)

    assert advanced.current_cursor == "cursor:next"
    assert advanced.next_page_no == 1
    assert advanced.raw_item_count == 2
    assert advanced.processed_item_count == 1
    assert advanced.quarantine_count == 1
    assert advanced.traversal_complete is False


def test_exact_item_processing_and_page_commit_are_replay_safe(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _, plan = _plan()
    _, current, request, page = _start(store, plan)
    receipt_id = _register_raw(path, b"success receipt")
    store.record_attempt(page.page_id, receipt_id, action="accept", failure_code=None, recorded_at=NOW)
    store.save_decoded(
        page.page_id,
        _decoded(request, receipt_id=receipt_id, items=(_item(0, "10.1234/A"),)),
        NOW,
    )

    first = store.mark_processed(
        page.page_id,
        0,
        canonical_doi="10.1234/a",
        outcome_ref="provider-revision:test",
        processed_at=NOW,
    )
    replay = store.mark_processed(
        page.page_id,
        0,
        canonical_doi="10.1234/a",
        outcome_ref="provider-revision:test",
        processed_at=NOW,
    )
    committed = store.commit_page(current.pass_id, page.page_id, NOW)
    again = store.commit_page(current.pass_id, page.page_id, NOW + timedelta(seconds=1))

    assert first is False
    assert replay is True
    assert again == committed

    with pytest.raises(CrossrefHarvestJournalError, match="crossref_item_outcome_conflict"):
        store.mark_processed(
            page.page_id,
            0,
            canonical_doi="10.1234/a",
            outcome_ref="provider-revision:other",
            processed_at=NOW,
        )


def test_duplicate_canonical_doi_is_counted_not_rejected(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    source, plan = _plan()
    _, current, request, page = _start(store, plan)
    receipt_id = _register_raw(path, b"page one")
    store.record_attempt(page.page_id, receipt_id, action="accept", failure_code=None, recorded_at=NOW)
    store.save_decoded(
        page.page_id,
        _decoded(
            request,
            receipt_id=receipt_id,
            total=3,
            items=(_item(0, "10.1234/A"), _item(1, "10.1234/B")),
        ),
        NOW,
    )
    store.mark_processed(page.page_id, 0, canonical_doi="10.1234/a", outcome_ref="r:a", processed_at=NOW)
    store.mark_processed(page.page_id, 1, canonical_doi="10.1234/b", outcome_ref="r:b", processed_at=NOW)
    current = store.commit_page(current.pass_id, page.page_id, NOW)

    second_request = source.page(plan, current.current_cursor)
    second_page = store.begin_page(current.pass_id, second_request, NOW)
    second_receipt = _register_raw(path, b"page two")
    store.record_attempt(
        second_page.page_id,
        second_receipt,
        action="accept",
        failure_code=None,
        recorded_at=NOW,
    )
    store.save_decoded(
        second_page.page_id,
        _decoded(
            second_request,
            receipt_id=second_receipt,
            cursor_out=None,
            total=3,
            items=(_item(0, "10.1234/A"),),
            end=True,
        ),
        NOW,
    )
    store.mark_processed(
        second_page.page_id,
        0,
        canonical_doi="10.1234/a",
        outcome_ref="r:a2",
        processed_at=NOW,
    )
    final = store.commit_page(current.pass_id, second_page.page_id, NOW)

    assert final.traversal_complete is True
    assert final.accounting_complete is True
    assert final.raw_item_count == 3
    assert final.unique_doi_count == 2
    assert final.duplicate_doi_count == 1
    assert final.drift_suspected is False
    assert final.repair_pending is False
    assert final.source_completeness == "provisional"


def test_quarantine_or_total_drift_finishes_accounting_but_requires_repair(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _, plan = _plan()
    window, current, request, page = _start(store, plan)
    receipt_id = _register_raw(path, b"short page")
    store.record_attempt(page.page_id, receipt_id, action="accept", failure_code=None, recorded_at=NOW)
    store.save_decoded(
        page.page_id,
        _decoded(
            request,
            receipt_id=receipt_id,
            cursor_out=None,
            total=9,
            items=(_item(0, None, quarantined=True),),
            end=True,
        ),
        NOW,
    )

    final = store.commit_page(current.pass_id, page.page_id, NOW)
    refreshed = store.read_window(window.window_id)

    assert final.traversal_complete is True
    assert final.accounting_complete is True
    assert final.parse_gap_count == 1
    assert final.drift_suspected is True
    assert final.repair_pending is True
    assert final.source_completeness == "unknown"
    assert refreshed.state == "repair_pending"


def test_multi_page_cursor_cycle_is_rejected_without_advancing_pass(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    source, plan = _plan()
    _, current, first_request, first_page = _start(store, plan)
    first_receipt = _register_raw(path, b"page one")
    store.record_attempt(
        first_page.page_id,
        first_receipt,
        action="accept",
        failure_code=None,
        recorded_at=NOW,
    )
    store.save_decoded(
        first_page.page_id,
        _decoded(
            first_request,
            receipt_id=first_receipt,
            cursor_out="cursor:b",
            items=(_item(0, "10.1234/A"), _item(1, "10.1234/B")),
        ),
        NOW,
    )
    for ordinal, doi in enumerate(("10.1234/a", "10.1234/b")):
        store.mark_processed(
            first_page.page_id,
            ordinal,
            canonical_doi=doi,
            outcome_ref=f"r:{ordinal}",
            processed_at=NOW,
        )
    current = store.commit_page(current.pass_id, first_page.page_id, NOW)

    request = source.page(plan, current.current_cursor)
    page = store.begin_page(current.pass_id, request, NOW)
    receipt = _register_raw(path, b"page two")
    store.record_attempt(page.page_id, receipt, action="accept", failure_code=None, recorded_at=NOW)
    store.save_decoded(
        page.page_id,
        _decoded(
            request,
            receipt_id=receipt,
            cursor_out="*",
            items=(_item(0, "10.1234/C"), _item(1, "10.1234/D")),
        ),
        NOW,
    )
    for ordinal, doi in enumerate(("10.1234/c", "10.1234/d")):
        store.mark_processed(
            page.page_id,
            ordinal,
            canonical_doi=doi,
            outcome_ref=f"r:2:{ordinal}",
            processed_at=NOW,
        )

    with pytest.raises(CrossrefHarvestJournalError, match="crossref_cursor_cycle"):
        store.commit_page(current.pass_id, page.page_id, NOW)

    unchanged = store.read_pass(current.pass_id)
    assert unchanged.current_cursor == "cursor:b"
    assert unchanged.next_page_no == 1


def test_running_pass_rejects_parameter_change_until_explicit_failure(tmp_path: Path) -> None:
    _, store = _setup(tmp_path)
    _, plan = _plan()
    window = store.ensure_window(plan, NOW)
    first = store.start_pass(plan, NOW)
    _, alternate = _plan(contact="other@example.invalid")

    assert alternate.query_fingerprint == plan.query_fingerprint
    assert alternate.parameters_fingerprint != plan.parameters_fingerprint

    with pytest.raises(CrossrefHarvestJournalError, match="crossref_pass_parameters_mismatch"):
        store.start_pass(alternate, NOW)

    store.fail_pass(first.pass_id, "contact_changed", NOW)
    second = store.start_pass(alternate, NOW + timedelta(seconds=1))

    assert second.pass_no == first.pass_no + 1
    assert second.window_id == window.window_id
    assert second.current_cursor == "*"


def test_receipt_must_be_available_raw_object(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _, plan = _plan()
    _, _, _, page = _start(store, plan)

    with pytest.raises(CrossrefHarvestJournalError, match="crossref_receipt_object_mismatch"):
        store.record_attempt(
            page.page_id,
            "raw:" + "a" * 64,
            action="accept",
            failure_code=None,
            recorded_at=NOW,
        )

    object_id = _register_raw(path, b"receipt")
    connection = sqlite3.connect(path)
    connection.execute("UPDATE object_registry SET state='quarantined' WHERE object_id=?", (object_id,))
    connection.commit()
    connection.close()

    with pytest.raises(CrossrefHarvestJournalError, match="crossref_receipt_object_mismatch"):
        store.record_attempt(
            page.page_id,
            object_id,
            action="accept",
            failure_code=None,
            recorded_at=NOW,
        )


def test_mark_processed_rejects_silently_ignored_update(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _, plan = _plan()
    _, _, request, page = _start(store, plan)
    receipt_id = _register_raw(path, b"ignored update receipt")
    store.record_attempt(
        page.page_id,
        receipt_id,
        action="accept",
        failure_code=None,
        recorded_at=NOW,
    )
    store.save_decoded(
        page.page_id,
        _decoded(
            request,
            receipt_id=receipt_id,
            items=(_item(0, "10.1234/A"),),
        ),
        NOW,
    )

    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TRIGGER ignore_mark_processed "
        "BEFORE UPDATE OF outcome_state ON crossref_harvest_items "
        "WHEN OLD.outcome_state='pending' AND NEW.outcome_state='processed' "
        "BEGIN SELECT RAISE(IGNORE); END"
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        CrossrefHarvestJournalError,
        match="crossref_item_outcome_conflict",
    ):
        store.mark_processed(
            page.page_id,
            0,
            canonical_doi="10.1234/a",
            outcome_ref="provider-revision:test",
            processed_at=NOW,
        )

    pending = store.pending_items(page.page_id)
    assert [(item.ordinal, item.raw_doi) for item in pending] == [
        (0, "10.1234/A")
    ]

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        item = connection.execute(
            "SELECT outcome_state,canonical_doi,outcome_ref,processed_at "
            "FROM crossref_harvest_items WHERE page_id=? AND ordinal=0",
            (page.page_id,),
        ).fetchone()
    finally:
        connection.close()
    assert tuple(item) == ("pending", None, None, None)
