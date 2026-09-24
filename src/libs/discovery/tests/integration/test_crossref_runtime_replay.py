import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.kernel_crossref_capture_store_adapter import (
    KernelCrossrefCaptureStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_harvest_journal_adapter import (
    SqliteCrossrefHarvestJournalAdapter,
)
from libs.discovery.application.commands.advance_crossref_harvest_page import (
    AdvanceCrossrefHarvestPage,
)
from libs.discovery.application.commands.capture_crossref_page import CaptureCrossrefPage
from libs.discovery.application.queries.replay_crossref_capture import ReplayCrossrefCapture
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject


NOW = datetime(2026, 9, 24, 2, 0, tzinfo=timezone.utc)
START = datetime(2026, 9, 23, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def _plan():
    source = CrossrefSourceAdapter()
    plan = source.compile(
        CrossrefWindowInput(
            binding_key="personal:3:statistics:1:crossref",
            scope_query="statistical learning",
            from_index=START,
            until_index=END,
            contact_email="fixture@example.invalid",
            config_version="profile-query-v1",
            rows=2,
        )
    )
    return source, plan


def _body() -> bytes:
    return json.dumps(
        {
            "status": "ok",
            "message-type": "work-list",
            "message": {
                "items": [{"DOI": "10.1234/ABC", "title": ["Paper"]}],
                "total-results": 1,
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


class FakeTransport:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, plan, request):
        del plan, request
        self.calls += 1
        return CrossrefHttpCapture(
            200,
            (("content-type", "application/json"),),
            _body(),
            NOW,
            True,
            None,
        )


class AcceptLease:
    def observe(self, status, headers, *, capture_error=None):
        assert status == 200
        assert headers == (("content-type", "application/json"),)
        assert capture_error is None
        return CrossrefRateDecision(
            "accept",
            None,
            0.0,
            1.0,
            None,
            None,
            (),
            False,
        )


class FakeGate:
    @contextmanager
    def slot(self, contact_email):
        assert contact_email == "fixture@example.invalid"
        yield AcceptLease()


class FailingReplay:
    def __call__(self, plan, request, receipt_id):
        del plan, request, receipt_id
        raise CrossrefProtocolError("simulated_decode_crash")


class ForbiddenCapture:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, plan, request, *, attempt_key):
        del plan, request, attempt_key
        self.calls += 1
        raise AssertionError("restart must not contact Crossref after receipt is journaled")


def test_runtime_v11_replays_durable_receipt_after_decode_crash_without_refetch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == 12
    assert info.external_effects_enabled is False

    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=11)
    raw = SqliteConnectionFactory(root)
    files = FilesystemObjectBytesAdapter(root)
    objects = SqliteObjectUnitOfWorkAdapter(raw)
    publish = PublishObject(files, objects)
    read = ReadObject(files, objects)
    capture_store = KernelCrossrefCaptureStoreAdapter(publish, read)

    source, plan = _plan()
    journal = SqliteCrossrefHarvestJournalAdapter(schema.connect)
    transport = FakeTransport()
    first = AdvanceCrossrefHarvestPage(
        source,
        CaptureCrossrefPage(
            transport,
            FakeGate(),
            capture_store,
            source=source,
            enabled=True,
        ),
        FailingReplay(),
        journal,
    )

    interrupted = first(plan, now=NOW)

    assert interrupted.state == "replay_failed"
    assert interrupted.receipt_id is not None
    assert interrupted.error_code == "simulated_decode_crash"
    assert transport.calls == 1

    page = journal.resume_page(interrupted.pass_id)
    assert page is not None
    assert page.state == "captured"
    assert page.successful_receipt_id == interrupted.receipt_id
    assert page.last_error_code == "simulated_decode_crash"

    connection = raw.connect()
    try:
        rows = connection.execute(
            "SELECT object_id,kind,state FROM object_registry "
            "WHERE object_id IN (?,?) ORDER BY object_id",
            (
                interrupted.receipt_id,
                "raw:" + hashlib.sha256(_body()).hexdigest(),
            ),
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 2
    assert all(row["kind"] == "raw" and row["state"] == "available" for row in rows)

    forbidden = ForbiddenCapture()
    reopened = SqliteCrossrefHarvestJournalAdapter(schema.connect)
    second = AdvanceCrossrefHarvestPage(
        source,
        forbidden,
        ReplayCrossrefCapture(capture_store, source),
        reopened,
    )
    resumed = second(plan, now=NOW + timedelta(seconds=1))

    assert resumed.state == "projection_required"
    assert resumed.pending_item_count == 1
    assert forbidden.calls == 0

    pending = reopened.pending_items(resumed.page_id)
    assert len(pending) == 1
    assert pending[0].raw_doi == "10.1234/ABC"
    reopened.mark_processed(
        resumed.page_id,
        pending[0].ordinal,
        canonical_doi="10.1234/abc",
        outcome_ref="provider-revision:crossref:test",
        processed_at=NOW + timedelta(seconds=2),
    )

    completed = second(plan, now=NOW + timedelta(seconds=3))

    assert completed.state == "pass_completed"
    assert forbidden.calls == 0
    pass_state = reopened.read_pass(completed.pass_id)
    window_state = reopened.read_window(completed.window_id)
    assert pass_state.state == "completed"
    assert pass_state.traversal_complete is True
    assert pass_state.accounting_complete is True
    assert pass_state.raw_item_count == 1
    assert pass_state.processed_item_count == 1
    assert pass_state.quarantine_count == 0
    assert pass_state.unique_doi_count == 1
    assert pass_state.duplicate_doi_count == 0
    assert pass_state.drift_suspected is False
    assert pass_state.repair_pending is False
    assert pass_state.source_completeness == "provisional"
    assert window_state.state == "traversed"

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM crossref_harvest_page_attempts"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM crossref_harvest_pages"
        ).fetchone()[0] == "accounted"
        assert connection.execute(
            "SELECT outcome_state FROM crossref_harvest_items"
        ).fetchone()[0] == "processed"
    finally:
        connection.close()
