from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.delivery.adapters.driven.sqlite_delivery_subscription_store_adapter import (
    SqliteDeliverySubscriptionStoreAdapter,
)
from libs.delivery.dtos.delivery_subscription import (
    ConfigureDeliverySubscriptionRequest,
)
from libs.delivery.exceptions.delivery_subscription_error import (
    DeliverySubscriptionError,
)
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.research_workflow.adapters.driven.sqlite_scheduler_input_adapter import (
    SqliteSchedulerInputAdapter,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _request(**changes):
    values = {
        "reader_id": "reader:local",
        "timezone": "Asia/Taipei",
        "local_time": "08:00",
        "max_items": 5,
        "recipient_ref": "recipient:primary",
        "enabled": True,
    }
    values.update(changes)
    return ConfigureDeliverySubscriptionRequest(**values)


def _setup(tmp_path: Path):
    root = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=7)
    return (
        schema,
        info,
        SqliteDeliverySubscriptionStoreAdapter(schema.connect),
    )


def test_configure_is_idempotent_and_scheduler_reads_canonical_daily_schedule(
    tmp_path: Path,
) -> None:
    schema, info, store = _setup(tmp_path)

    first = store.configure(_request(), now=NOW)
    replay = store.configure(_request(), now=NOW)

    assert first.subscription_id == replay.subscription_id
    assert first.policy_version == replay.policy_version == 1
    assert first.replayed is False
    assert replay.replayed is True
    snapshot = SqliteSchedulerInputAdapter(schema.connect).read(NOW)
    assert len(snapshot.delivery_schedules) == 1
    schedule = snapshot.delivery_schedules[0]
    assert schedule.subscription_id == first.subscription_id
    assert schedule.timezone == "Asia/Taipei"
    assert schedule.local_time == "08:00"
    assert info.external_effects_enabled is False


def test_change_bumps_policy_version_but_preserves_identity_and_created_at(
    tmp_path: Path,
) -> None:
    _, _, store = _setup(tmp_path)
    first = store.configure(_request(enabled=False), now=NOW)
    changed = store.configure(
        _request(
            enabled=True,
            local_time="09:30",
            max_items=7,
            recipient_ref="recipient:new",
        ),
        now=NOW,
    )

    assert changed.subscription_id == first.subscription_id
    assert changed.created_at == first.created_at
    assert changed.policy_version == 2
    assert changed.enabled is True
    assert changed.local_time == "09:30"
    assert changed.max_items == 7
    assert changed.recipient_ref == "recipient:new"


def test_disable_removes_future_schedule_without_touching_effects_gate(
    tmp_path: Path,
) -> None:
    schema, info, store = _setup(tmp_path)
    store.configure(_request(), now=NOW)
    disabled = store.configure(_request(enabled=False), now=NOW)

    assert disabled.enabled is False
    assert SqliteSchedulerInputAdapter(schema.connect).read(
        NOW
    ).delivery_schedules == ()
    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT external_effects_enabled FROM workspace_metadata "
            "WHERE singleton=1"
        ).fetchone()[0] == 0
    finally:
        connection.close()
    assert info.external_effects_enabled is False


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"timezone": "Mars/Olympus"}, "invalid_delivery_timezone"),
        ({"local_time": "24:00"}, "invalid_delivery_local_time"),
        ({"local_time": "8:00"}, "invalid_delivery_local_time"),
        ({"max_items": 0}, "invalid_delivery_max_items"),
        ({"max_items": 101}, "invalid_delivery_max_items"),
        ({"max_items": True}, "invalid_delivery_max_items"),
        ({"recipient_ref": "bad\nref"}, "invalid_delivery_recipient_ref"),
        ({"enabled": 1}, "invalid_delivery_enabled"),
    ],
)
def test_invalid_configuration_has_zero_mutation(
    tmp_path: Path,
    changes: dict[str, object],
    code: str,
) -> None:
    schema, _, store = _setup(tmp_path)

    with pytest.raises(DeliverySubscriptionError, match=code):
        store.configure(_request(**changes), now=NOW)

    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM delivery_subscriptions"
        ).fetchone()[0] == 0
    finally:
        connection.close()
