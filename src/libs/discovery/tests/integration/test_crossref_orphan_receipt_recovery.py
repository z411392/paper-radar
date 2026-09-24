import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.kernel_crossref_capture_store_adapter import (
    KernelCrossrefCaptureStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_harvest_journal_adapter import (
    SqliteCrossrefHarvestJournalAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_orphan_receipt_recovery_adapter import (
    SqliteCrossrefOrphanReceiptRecoveryAdapter,
)
from libs.discovery.application.commands.advance_crossref_harvest_page import (
    AdvanceCrossrefHarvestPage,
)
from libs.discovery.application.commands.capture_crossref_page import CaptureCrossrefPage
from libs.discovery.application.queries.replay_crossref_capture import ReplayCrossrefCapture
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision
from libs.discovery.exceptions.crossref_capture_recovery_error import (
    CrossrefCaptureRecoveryError,
)
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


def _plan(*, scope="statistical learning"):
    source = CrossrefSourceAdapter()
    plan = source.compile(
        CrossrefWindowInput(
            binding_key="personal:3:statistics:1:crossref",
            scope_query=scope,
            from_index=START,
            until_index=END,
            contact_email="fixture@example.invalid",
            config_version="profile-query-v1",
            rows=2,
        )
    )
    return source, plan


def _body(title: str = "Paper") -> bytes:
    return json.dumps(
        {
            "status": "ok",
            "message-type": "work-list",
            "message": {
                "items": [{"DOI": "10.1234/ABC", "title": [title]}],
                "total-results": 1,
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


class Transport:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    def get(self, plan, request):
        del plan, request
        self.calls += 1
        return CrossrefHttpCapture(
            200,
            (("content-type", "application/json"),),
            self.body,
            NOW,
            True,
            None,
        )


class AcceptLease:
    def __init__(self, owner) -> None:
        self.owner = owner

    def observe(self, status, headers, *, capture_error=None):
        self.owner.observations += 1
        assert status == 200
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


class Gate:
    def __init__(self) -> None:
        self.slots = 0
        self.observations = 0

    @contextmanager
    def slot(self, contact_email):
        assert contact_email == "fixture@example.invalid"
        self.slots += 1
        yield AcceptLease(self)


class ForbiddenCapture:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, plan, request, *, attempt_key):
        del plan, request, attempt_key
        self.calls += 1
        raise AssertionError("orphan recovery must run before provider refetch")


def _workspace(tmp_path: Path):
    root = tmp_path / "workspace"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == 13

    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=11)
    raw = SqliteConnectionFactory(root)
    files = FilesystemObjectBytesAdapter(root)
    objects = SqliteObjectUnitOfWorkAdapter(raw)
    publish = PublishObject(files, objects)
    read = ReadObject(files, objects)
    captures = KernelCrossrefCaptureStoreAdapter(publish, read)
    return root, schema, raw, publish, read, captures


def _requested_page(schema, source, plan):
    journal = SqliteCrossrefHarvestJournalAdapter(schema.connect)
    journal.ensure_window(plan, NOW)
    state = journal.start_pass(plan, NOW)
    request = source.page(plan, state.current_cursor)
    page = journal.begin_page(state.pass_id, request, NOW)
    assert page.state == "requested"
    return journal, state, request, page


def _orphan_receipt(captures, source, plan, request, page_id, *, title="Paper"):
    gate = Gate()
    transport = Transport(_body(title))
    result = CaptureCrossrefPage(
        transport,
        gate,
        captures,
        source=source,
        enabled=True,
    )(plan, request, attempt_key=page_id)
    assert transport.calls == 1
    assert gate.observations == 1
    return result.receipt_id


def test_unique_orphan_is_attached_and_replayed_without_provider_refetch(
    tmp_path: Path,
) -> None:
    _, schema, _, _, read, captures = _workspace(tmp_path)
    source, plan = _plan()
    journal, state, request, page = _requested_page(schema, source, plan)
    orphan = _orphan_receipt(captures, source, plan, request, page.page_id)

    reopened = SqliteCrossrefHarvestJournalAdapter(schema.connect)
    reconcile_gate = Gate()
    recovery = SqliteCrossrefOrphanReceiptRecoveryAdapter(
        schema.connect,
        read,
        captures,
        reconcile_gate,
    )
    forbidden = ForbiddenCapture()
    command = AdvanceCrossrefHarvestPage(
        source,
        forbidden,
        ReplayCrossrefCapture(captures, source),
        reopened,
        recovery,
    )

    resumed = command(plan, now=NOW + timedelta(seconds=2))

    assert resumed.state == "projection_required"
    assert resumed.receipt_id == orphan
    assert resumed.pending_item_count == 1
    assert forbidden.calls == 0
    assert reconcile_gate.slots == 1
    assert reconcile_gate.observations == 1
    page_after = reopened.resume_page(state.pass_id)
    assert page_after is not None
    assert page_after.successful_receipt_id == orphan
    assert page_after.attempt_count == 1

    pending = reopened.pending_items(page.page_id)
    reopened.mark_processed(
        page.page_id,
        pending[0].ordinal,
        canonical_doi="10.1234/abc",
        outcome_ref="provider-revision:orphan",
        processed_at=NOW + timedelta(seconds=3),
    )
    complete = command(plan, now=NOW + timedelta(seconds=4))
    assert complete.state == "pass_completed"
    assert forbidden.calls == 0


def test_multiple_matching_orphans_are_ambiguous_and_never_refetched(
    tmp_path: Path,
) -> None:
    _, schema, _, _, read, captures = _workspace(tmp_path)
    source, plan = _plan()
    _, _, request, page = _requested_page(schema, source, plan)
    first = captures.save(
        request,
        CrossrefHttpCapture(
            200,
            (("content-type", "application/json"),),
            _body("first"),
            NOW,
            True,
            None,
        ),
        attempt_key=page.page_id,
    )
    second = captures.save(
        request,
        CrossrefHttpCapture(
            200,
            (("content-type", "application/json"),),
            _body("second"),
            NOW,
            True,
            None,
        ),
        attempt_key=page.page_id,
    )
    assert first != second
    recovery = SqliteCrossrefOrphanReceiptRecoveryAdapter(
        schema.connect,
        read,
        captures,
        Gate(),
    )

    with pytest.raises(
        CrossrefCaptureRecoveryError,
        match="crossref_orphan_receipt_ambiguous",
    ):
        recovery.find(plan, page.page_id, request)


def test_incomplete_orphan_scan_fails_closed_instead_of_refetching(
    tmp_path: Path,
) -> None:
    _, schema, _, publish, read, captures = _workspace(tmp_path)
    source, plan = _plan()
    _, _, request, page = _requested_page(schema, source, plan)
    for index in range(3):
        publish(
            f"unrelated-{index}".encode("ascii"),
            "raw",
            "application/octet-stream",
            "source-response",
        )
    recovery = SqliteCrossrefOrphanReceiptRecoveryAdapter(
        schema.connect,
        read,
        captures,
        Gate(),
        maximum_scan=2,
    )

    with pytest.raises(
        CrossrefCaptureRecoveryError,
        match="crossref_orphan_scan_incomplete",
    ):
        recovery.find(plan, page.page_id, request)


def test_same_attempt_key_with_different_request_is_rejected(
    tmp_path: Path,
) -> None:
    _, schema, _, _, read, captures = _workspace(tmp_path)
    source, plan = _plan()
    _, _, request, page = _requested_page(schema, source, plan)
    _, other_plan = _plan(scope="different scope")
    other_request = source.page(other_plan)
    captures.save(
        other_request,
        CrossrefHttpCapture(
            200,
            (("content-type", "application/json"),),
            _body(),
            NOW,
            True,
            None,
        ),
        attempt_key=page.page_id,
    )
    recovery = SqliteCrossrefOrphanReceiptRecoveryAdapter(
        schema.connect,
        read,
        captures,
        Gate(),
    )

    with pytest.raises(
        CrossrefCaptureRecoveryError,
        match="crossref_orphan_request_mismatch",
    ):
        recovery.find(plan, page.page_id, request)
