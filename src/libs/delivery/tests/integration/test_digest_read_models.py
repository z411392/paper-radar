import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.delivery.adapters.driven.sqlite_digest_delivery_context_adapter import (
    SqliteDigestDeliveryContextAdapter,
)
from libs.paper_explanations.adapters.driven.sqlite_digest_current_summary_adapter import (
    SqliteDigestCurrentSummaryAdapter,
)
from libs.paper_explanations.exceptions.digest_summary_read_error import DigestSummaryReadError
from libs.scholarly_catalog.adapters.driven.sqlite_digest_research_event_adapter import (
    SqliteDigestResearchEventAdapter,
)
from libs.watch_profiles.adapters.driven.sqlite_digest_relevance_adapter import (
    SqliteDigestRelevanceAdapter,
)


START = datetime(2026, 9, 23, 16, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)
WORK = "work:test"
REVISION = "revision:test"
SNAPSHOT = "snapshot:test"
SUMMARY = "summary:test"
RUN = "run:test"
GENERATION = "a" * 64
OUTPUT_DIGEST = "b" * 64
OUTPUT = "model_output:" + OUTPUT_DIGEST


class FakeReadObject:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = []

    def __call__(self, object_id: str) -> bytes:
        self.calls.append(object_id)
        assert object_id == OUTPUT
        return self.content


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    return factory


def _object(
    connection: sqlite3.Connection,
    kind: str,
    digest: str,
) -> str:
    object_id = f"{kind}:{digest}"
    connection.execute(
        "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
        (
            object_id,
            digest,
            f"objects/{kind}/{digest[:2]}/{digest}",
            kind,
            "application/json",
            10,
            "available",
            START.isoformat(),
            "test",
        ),
    )
    return object_id


def _artifact(*, revision_id: str = REVISION) -> bytes:
    return json.dumps(
        {
            "format_version": 1,
            "snapshot_id": SNAPSHOT,
            "revision_id": revision_id,
            "work_id": WORK,
            "generation_fingerprint": GENERATION,
            "generation_input_fingerprint": "c" * 64,
            "language": "zh-TW",
            "explanation_profile": "plain-zh-TW-v1",
            "evidence_level": "abstract_only",
            "original_abstract": "abstract",
            "faithful_translation": [],
            "plain_language_card": [
                {
                    "claim_type": "result",
                    "text": "白話結果",
                    "claim_ids": ["claim:1"],
                }
            ],
            "not_reported_in_read_evidence": [],
            "claim_ids": ["claim:1"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _setup(tmp_path: Path) -> Path:
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    root = Path(__file__).resolve().parents[5]
    for name in (
        "0001-object-registry.sql",
        "0002-watch-profiles.sql",
        "0003-scholarly-catalog.sql",
        "0005-paper-explanations.sql",
        "0007-delivery.sql",
        "0009-relevance-assessment-domains.sql",
    ):
        connection.executescript((root / "migrations" / name).read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO workspace_metadata VALUES(1,'workspace:test',9,0,?,NULL)",
        (START.isoformat(),),
    )

    evidence_object = _object(connection, "evidence", "d" * 64)
    text_object = _object(connection, "extracted", "e" * 64)
    _object(connection, "model_output", OUTPUT_DIGEST)

    connection.execute(
        "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
        (WORK, "Paper title", "preprint", None, None, START.isoformat(), START.isoformat()),
    )
    connection.execute(
        "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
        (
            "manifest:test",
            WORK,
            "arxiv",
            "1234.5678",
            "preprint",
            "https://arxiv.org/abs/1234.5678",
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO paper_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            REVISION,
            "manifest:test",
            WORK,
            "v1",
            "f" * 64,
            "Paper title",
            None,
            None,
            "2026-09-23",
            "day",
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            SNAPSHOT,
            REVISION,
            WORK,
            evidence_object,
            text_object,
            "parser-v1",
            "abstract_only",
            "{}",
            "1" * 64,
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO model_runs("
        "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,state,started_at"
        ") VALUES(?,?,?,?,?,?,?,?)",
        (
            RUN,
            "abstract_reading_card",
            "openrouter",
            "model",
            "2" * 64,
            GENERATION,
            "succeeded",
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO summary_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            SUMMARY,
            REVISION,
            WORK,
            SNAPSHOT,
            GENERATION,
            RUN,
            OUTPUT,
            "zh-TW",
            "plain-zh-TW-v1",
            "passed",
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO current_summaries VALUES(?,?,?,?,?,?,?)",
        (
            WORK,
            "zh-TW",
            "plain-zh-TW-v1",
            SUMMARY,
            REVISION,
            GENERATION,
            1,
        ),
    )

    connection.execute(
        "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
        (
            "event:new",
            WORK,
            REVISION,
            "new_work",
            "event-key:new",
            "{}",
            None,
            "2026-09-24T07:30:00+08:00",
        ),
    )
    connection.execute(
        "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
        (
            "event:correction",
            WORK,
            None,
            "correction",
            "event-key:correction",
            "{}",
            None,
            "2026-09-24T07:40:00+08:00",
        ),
    )

    connection.execute(
        "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
        ("statistics", "統計學", '{"sources":["arxiv"]}', 7, START.isoformat()),
    )
    connection.execute(
        "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
        ("profile:current", "reader:local", "Current", "active", None, START.isoformat()),
    )
    connection.execute(
        "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
        (
            "profile:current",
            3,
            "scope",
            '{"sources":["arxiv"]}',
            "3" * 64,
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
        ("profile:current", 3, "statistics", 7),
    )
    connection.execute(
        "UPDATE watch_profiles SET published_revision=3 WHERE id='profile:current'"
    )
    connection.execute(
        "INSERT INTO relevance_assessments VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "relevance:current",
            "profile:current",
            3,
            REVISION,
            "4" * 64,
            "direct",
            "succeeded",
            '{"anchor_ids":["anchor:1"],"recommendation_reason":"related"}',
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO relevance_assessment_domains VALUES(?,?,?,?)",
        ("relevance:current", "statistics", 7, SNAPSHOT),
    )

    connection.execute(
        "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "subscription:daily",
            "reader:local",
            "email",
            1,
            "Asia/Taipei",
            '{"kind":"daily","local_time":"08:00"}',
            5,
            "recipient:primary",
            1,
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO digests VALUES(?,?,?,?,?,'queued',?)",
        (
            "digest:test",
            "subscription:daily",
            "prior",
            START.isoformat(),
            None,
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES(?,?,?,?, 'pending',9,NULL,?)",
        (
            "outbox:test",
            "digest:test",
            "request:test",
            "5" * 64,
            START.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO notification_ledger VALUES(?,?,?,?,?,'reserved',?)",
        (
            "notification:test",
            "reader:local",
            "event:new",
            "email",
            "outbox:test",
            START.isoformat(),
        ),
    )
    connection.commit()
    connection.close()
    return path


def test_owner_read_models_return_only_digest_safe_current_data(tmp_path: Path) -> None:
    path = _setup(tmp_path)
    connect = _connect(path)
    events = SqliteDigestResearchEventAdapter(connect)(START, CUTOFF)

    assert [event.event_id for event in events] == ["event:new", "event:correction"]
    assert events[0].observed_at == datetime(2026, 9, 23, 23, 30, tzinfo=timezone.utc)
    assert events[0].title == "Paper title"
    assert events[1].revision_id is None
    assert events[1].event_kind == "correction"
    assert events[1].title == "Paper title"
    assert events[1].source_url == "https://arxiv.org/abs/1234.5678"

    reader = FakeReadObject(_artifact())
    summary = SqliteDigestCurrentSummaryAdapter(connect, reader)(WORK, REVISION)
    assert summary is not None
    assert summary.summary_id == SUMMARY
    assert summary.snapshot_id == SNAPSHOT
    assert summary.plain_language == ("白話結果",)
    assert reader.calls == [OUTPUT]

    relevance = SqliteDigestRelevanceAdapter(connect)(
        "reader:local",
        REVISION,
        SNAPSHOT,
    )
    assert [(item.domain_id, item.decision) for item in relevance] == [
        ("statistics", "direct")
    ]

    delivery = SqliteDigestDeliveryContextAdapter(connect)
    context = delivery.load("subscription:daily")
    assert (context.reader_id, context.workspace_epoch, context.max_items) == (
        "reader:local",
        9,
        5,
    )
    assert delivery.already_notified(
        "reader:local",
        "email",
        ("event:new", "event:other"),
    ) == frozenset({"event:new"})


def test_summary_artifact_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    path = _setup(tmp_path)
    adapter = SqliteDigestCurrentSummaryAdapter(
        _connect(path),
        FakeReadObject(_artifact(revision_id="revision:other")),
    )

    with pytest.raises(DigestSummaryReadError, match="digest_summary_artifact_mismatch"):
        adapter(WORK, REVISION)


def test_corrupt_generation_run_cannot_be_used_for_digest(tmp_path: Path) -> None:
    path = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE model_runs SET state='failed' WHERE id=?", (RUN,))
    connection.commit()
    connection.close()
    adapter = SqliteDigestCurrentSummaryAdapter(_connect(path), FakeReadObject(_artifact()))

    with pytest.raises(DigestSummaryReadError, match="digest_summary_corrupt"):
        adapter(WORK, REVISION)


def test_relevance_read_requires_exact_summary_snapshot(tmp_path: Path) -> None:
    path = _setup(tmp_path)
    adapter = SqliteDigestRelevanceAdapter(_connect(path))

    assert adapter("reader:local", REVISION, "snapshot:other") == ()
