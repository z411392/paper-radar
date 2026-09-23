import json
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.watch_profiles.adapters.driven.sqlite_relevance_assessment_store_adapter import (
    SqliteRelevanceAssessmentStoreAdapter,
)
from libs.watch_profiles.application.commands.persist_relevance_assessment import (
    PersistRelevanceAssessment,
)
from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment
from libs.watch_profiles.exceptions.relevance_persistence_error import RelevancePersistenceError


NOW = datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc)
PROFILE = "personal"
DOMAIN = "badminton"
WORK = "work:test"
REVISION = "revision:test"
SNAPSHOT = "snapshot:test"
ANCHOR = "anchor:test"
FINGERPRINT = "a" * 64


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "relevance.sqlite3"
    connection = sqlite3.connect(path)
    root = Path(__file__).resolve().parents[5]
    for name in (
        "0001-object-registry.sql",
        "0002-watch-profiles.sql",
        "0003-scholarly-catalog.sql",
        "0005-paper-explanations.sql",
        "0009-relevance-assessment-domains.sql",
    ):
        connection.executescript((root / "migrations" / name).read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
        (DOMAIN, "羽球", '{"sources":["arxiv"]}', 2, NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
        ("statistics", "統計學", '{"sources":["arxiv"]}', 7, NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
        (PROFILE, "local", "mine", "active", None, NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
        (
            PROFILE,
            3,
            "scope",
            '{"sources":["arxiv"]}',
            "b" * 64,
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
        (PROFILE, 3, DOMAIN, 2),
    )
    connection.execute(
        "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
        (PROFILE, 3, "statistics", 7),
    )
    connection.execute(
        "UPDATE watch_profiles SET published_revision=3 WHERE id=?",
        (PROFILE,),
    )
    connection.execute(
        "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
        (WORK, "paper", "preprint", None, None, NOW.isoformat(), NOW.isoformat()),
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
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO paper_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            REVISION,
            "manifest:test",
            WORK,
            "v1",
            "c" * 64,
            "paper",
            None,
            NOW.isoformat(),
            NOW.date().isoformat(),
            "day",
            NOW.isoformat(),
        ),
    )
    for digest, kind in (("d" * 64, "evidence"), ("e" * 64, "extracted")):
        connection.execute(
            "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
            (
                f"{kind}:{digest}",
                digest,
                f"objects/{kind}/{digest[:2]}/{digest}",
                kind,
                "application/json",
                10,
                "available",
                NOW.isoformat(),
                "test",
            ),
        )
    connection.execute(
        "INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?,?,?)",
        (
            SNAPSHOT,
            REVISION,
            WORK,
            "evidence:" + "d" * 64,
            "extracted:" + "e" * 64,
            "parser-v1",
            "abstract_only",
            "{}",
            "f" * 64,
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO evidence_anchors VALUES(?,?,?,?,?,?,?,?,?)",
        (ANCHOR, SNAPSHOT, "abstract", "p1", "羽球", 0, 2, None),
    )
    connection.commit()
    connection.close()
    store = SqliteRelevanceAssessmentStoreAdapter(_connect(path))
    return path, PersistRelevanceAssessment(store)


def assessment(**changes) -> RelevanceAssessment:
    base = RelevanceAssessment(
        FINGERPRINT,
        PROFILE,
        3,
        DOMAIN,
        2,
        SNAPSHOT,
        "succeeded",
        "direct",
        "原文直接研究羽球。",
        (ANCHOR,),
        None,
    )
    return replace(base, **changes)


def test_success_persists_relational_domain_identity_and_safe_reason(tmp_path: Path) -> None:
    path, persist = _setup(tmp_path)

    result = persist(assessment(), assessed_at=NOW)

    assert result.replayed is False
    connection = sqlite3.connect(path)
    row = connection.execute(
        "SELECT profile_id,profile_revision,revision_id,input_fingerprint,"
        "decision,execution_state,reason_json FROM relevance_assessments WHERE id=?",
        (result.assessment_id,),
    ).fetchone()
    assert row[:6] == (PROFILE, 3, REVISION, FINGERPRINT, "direct", "succeeded")
    reason = json.loads(row[6])
    assert reason["recommendation_reason"] == "原文直接研究羽球。"
    assert reason["anchor_ids"] == [ANCHOR]
    assert connection.execute(
        "SELECT domain_id,domain_revision,snapshot_id "
        "FROM relevance_assessment_domains WHERE assessment_id=?",
        (result.assessment_id,),
    ).fetchone() == (DOMAIN, 2, SNAPSHOT)
    connection.close()


def test_exact_replay_does_not_add_rows(tmp_path: Path) -> None:
    path, persist = _setup(tmp_path)

    first = persist(assessment(), assessed_at=NOW)
    replay = persist(assessment(), assessed_at=NOW)

    assert replay.assessment_id == first.assessment_id
    assert replay.replayed is True
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM relevance_assessments").fetchone()[0] == 1
    assert connection.execute(
        "SELECT count(*) FROM relevance_assessment_domains"
    ).fetchone()[0] == 1
    connection.close()


def test_failed_exact_input_can_transition_to_succeeded_after_retry(tmp_path: Path) -> None:
    path, persist = _setup(tmp_path)
    failed = assessment(
        execution_state="failed",
        decision=None,
        recommendation_reason=None,
        anchor_ids=(),
        error_code="timeout",
    )

    first = persist(failed, assessed_at=NOW)
    second = persist(assessment(), assessed_at=NOW.replace(minute=5))

    assert second.assessment_id == first.assessment_id
    assert second.replayed is False
    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT execution_state,decision FROM relevance_assessments WHERE id=?",
        (first.assessment_id,),
    ).fetchone() == ("succeeded", "direct")
    connection.close()


def test_succeeded_exact_input_cannot_be_rewritten_with_another_answer(tmp_path: Path) -> None:
    _, persist = _setup(tmp_path)
    persist(assessment(), assessed_at=NOW)

    with pytest.raises(RelevancePersistenceError, match="relevance_result_conflict"):
        persist(
            assessment(decision="adjacent", recommendation_reason="另一個答案。"),
            assessed_at=NOW.replace(minute=5),
        )


def test_nonstale_result_requires_current_active_profile_revision(tmp_path: Path) -> None:
    path, persist = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE watch_profiles SET lifecycle='paused' WHERE id=?", (PROFILE,))
    connection.commit()
    connection.close()

    with pytest.raises(RelevancePersistenceError, match="relevance_not_current"):
        persist(assessment(), assessed_at=NOW)


def test_stale_result_can_be_preserved_as_history_but_has_no_decision(tmp_path: Path) -> None:
    path, persist = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE watch_profiles SET lifecycle='paused' WHERE id=?", (PROFILE,))
    connection.commit()
    connection.close()
    stale = assessment(
        execution_state="stale",
        decision=None,
        recommendation_reason=None,
        anchor_ids=(),
        error_code="profile_changed",
    )

    result = persist(stale, assessed_at=NOW)

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT execution_state,decision FROM relevance_assessments WHERE id=?",
        (result.assessment_id,),
    ).fetchone() == ("stale", None)
    connection.close()


def test_domain_snapshot_and_anchor_identity_are_rechecked(tmp_path: Path) -> None:
    _, persist = _setup(tmp_path)

    with pytest.raises(RelevancePersistenceError, match="relevance_domain_mismatch"):
        persist(assessment(domain_revision=3), assessed_at=NOW)
    with pytest.raises(RelevancePersistenceError, match="relevance_anchor_mismatch"):
        persist(assessment(anchor_ids=("anchor:other",)), assessed_at=NOW)


def test_same_input_fingerprint_in_another_selected_domain_gets_distinct_identity(
    tmp_path: Path,
) -> None:
    path, persist = _setup(tmp_path)
    first = persist(assessment(), assessed_at=NOW)
    second = persist(
        assessment(
            domain_id="statistics",
            domain_revision=7,
            decision="adjacent",
            recommendation_reason="與統計方法相關。",
        ),
        assessed_at=NOW,
    )

    assert first.assessment_id != second.assessment_id
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM relevance_assessments").fetchone()[0] == 2
    connection.close()


def test_concurrent_exact_replay_creates_one_projection(tmp_path: Path) -> None:
    path, persist = _setup(tmp_path)
    gate = threading.Barrier(2)
    results = []
    errors = []

    def worker() -> None:
        try:
            gate.wait(timeout=2)
            results.append(persist(assessment(), assessed_at=NOW))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert len(results) == 2
    assert {item.replayed for item in results} == {False, True}
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM relevance_assessments").fetchone()[0] == 1
    connection.close()
