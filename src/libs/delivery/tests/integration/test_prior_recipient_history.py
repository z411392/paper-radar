import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.delivery.adapters.driven.sqlite_prior_recipient_history_adapter import (
    SqlitePriorRecipientHistoryAdapter,
)
from libs.delivery.application.queries.check_prior_recipient import (
    CheckPriorRecipient,
)
from libs.delivery.exceptions.prior_recipient_error import PriorRecipientError


NOW = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)
CANONICAL = "work:canonical"
MID = "work:mid"
OLD = "work:old"


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(
            path,
            isolation_level=None,
            timeout=2,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    return factory


def _setup(tmp_path: Path) -> tuple[Path, CheckPriorRecipient]:
    path = tmp_path / "prior.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in (
        "0001-object-registry.sql",
        "0002-watch-profiles.sql",
        "0003-scholarly-catalog.sql",
        "0007-delivery.sql",
    ):
        connection.executescript(
            (root / "migrations" / name).read_text(encoding="utf-8")
        )
    connection.execute(
        "INSERT INTO workspace_metadata VALUES(1,'workspace:test',1,0,?,NULL)",
        (NOW.isoformat(),),
    )
    for work in (CANONICAL, MID, OLD, "work:other"):
        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                work,
                work,
                "published",
                None,
                None,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    connection.execute(
        "INSERT INTO work_aliases VALUES(?,?,?,?)",
        (MID, CANONICAL, "{}", NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO work_aliases VALUES(?,?,?,?)",
        (OLD, MID, "{}", NOW.isoformat()),
    )
    connection.commit()
    connection.close()
    return path, CheckPriorRecipient(
        SqlitePriorRecipientHistoryAdapter(_connect(path))
    )


def _notification(
    path: Path,
    *,
    suffix: str,
    work_id: str = OLD,
    reader_id: str = "reader:one",
    channel: str = "email",
    state: str = "accepted",
) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        event_id = "event:" + suffix
        subscription = "subscription:" + suffix
        digest = "digest:" + suffix
        outbox = "outbox:" + suffix
        connection.execute(
            "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
            (
                event_id,
                work_id,
                None,
                "correction",
                "event-key:" + suffix,
                "{}",
                None,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                subscription,
                reader_id,
                channel,
                1,
                "Asia/Taipei",
                "{}",
                5,
                "recipient:" + suffix,
                1,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO digests VALUES(?,?,?,?,?,'sent',?)",
            (
                digest,
                subscription,
                "period:" + suffix,
                NOW.isoformat(),
                None,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO delivery_outbox VALUES(?,?,?,?, 'provider_accepted',1,NULL,?)",
            (
                outbox,
                digest,
                "request:" + suffix,
                "a" * 64,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO notification_ledger VALUES(?,?,?,?,?,?,?)",
            (
                "notification:" + suffix,
                reader_id,
                event_id,
                channel,
                outbox,
                state,
                NOW.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize("state", ["accepted", "unknown"])
def test_merge_old_work_history_still_counts_as_prior_recipient(
    tmp_path: Path,
    state: str,
) -> None:
    path, query = _setup(tmp_path)
    _notification(path, suffix=state, state=state)

    assert query("reader:one", "email", CANONICAL) is True
    assert query("reader:one", "email", MID) is True
    assert query("reader:one", "email", OLD) is True


@pytest.mark.parametrize("state", ["reserved", "cancelled"])
def test_unsent_ledger_states_do_not_create_prior_recipient(
    tmp_path: Path,
    state: str,
) -> None:
    path, query = _setup(tmp_path)
    _notification(path, suffix=state, state=state)

    assert query("reader:one", "email", CANONICAL) is False


def test_reader_and_channel_are_exact(tmp_path: Path) -> None:
    path, query = _setup(tmp_path)
    _notification(path, suffix="accepted")

    assert query("reader:two", "email", CANONICAL) is False
    assert query("reader:one", "rss", CANONICAL) is False


def test_unrelated_work_does_not_qualify(tmp_path: Path) -> None:
    path, query = _setup(tmp_path)
    _notification(path, suffix="other", work_id="work:other")

    assert query("reader:one", "email", CANONICAL) is False


def test_alias_cycle_fails_closed(tmp_path: Path) -> None:
    path, query = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO work_aliases VALUES(?,?,?,?)",
        (CANONICAL, OLD, "{}", NOW.isoformat()),
    )
    connection.commit()
    connection.close()

    with pytest.raises(PriorRecipientError, match="prior_recipient_alias_cycle"):
        query("reader:one", "email", CANONICAL)


def test_alias_family_limit_fails_closed(tmp_path: Path) -> None:
    path, _ = _setup(tmp_path)
    query = CheckPriorRecipient(
        SqlitePriorRecipientHistoryAdapter(
            _connect(path),
            maximum_alias_family=2,
        )
    )

    with pytest.raises(
        PriorRecipientError,
        match="prior_recipient_alias_limit_exceeded",
    ):
        query("reader:one", "email", CANONICAL)


@pytest.mark.parametrize(
    "reader,channel,work,code",
    [
        ("", "email", CANONICAL, "invalid_prior_recipient_reader"),
        ("reader:one", "sms", CANONICAL, "invalid_prior_recipient_channel"),
        ("reader:one", "email", "", "invalid_prior_recipient_work"),
    ],
)
def test_invalid_query_inputs_fail_closed(
    tmp_path: Path,
    reader: str,
    channel: str,
    work: str,
    code: str,
) -> None:
    _, query = _setup(tmp_path)

    with pytest.raises(PriorRecipientError, match=code):
        query(reader, channel, work)
