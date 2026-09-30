import sqlite3
import threading
from pathlib import Path

import pytest

from libs.paper_explanations.adapters.driven.sqlite_current_summary_store_adapter import (
    SqliteCurrentSummaryStoreAdapter,
)
from libs.paper_explanations.application.commands.publish_current_summary import PublishCurrentSummary
from libs.paper_explanations.application.commands.publish_verified_current_summary import (
    PublishVerifiedCurrentSummary,
)
from libs.paper_explanations.dtos.explanation_persistence import PersistedExplanation
from libs.paper_explanations.exceptions.explanation_verification_error import ExplanationVerificationError

WORK = "work:" + "1" * 64
R1 = "revision:" + "2" * 64
R2 = "revision:" + "3" * 64
S1 = "summary:" + "4" * 64
S2 = "summary:" + "5" * 64
BAD = "summary:" + "6" * 64
S1_NEW = "summary:" + "7" * 64
RUN1 = "run:" + "8" * 64
RUN2 = "run:" + "9" * 64
RUN_BAD = "run:" + "a" * 64
RUN1_NEW = "run:" + "b" * 64
F1 = "c" * 64
F2 = "d" * 64
F_BAD = "e" * 64
F1_NEW = "f" * 64


def _connect(path: Path):
    def factory():
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _insert_run(
    connection: sqlite3.Connection,
    run_id: str,
    fingerprint: str,
    started_at: str,
    *,
    state: str = "succeeded",
) -> None:
    connection.execute(
        "INSERT INTO model_runs(id,state,input_fingerprint,started_at) VALUES(?,?,?,?)",
        (run_id, state, fingerprint, started_at),
    )


def _insert_summary(
    connection: sqlite3.Connection,
    summary_id: str,
    revision_id: str,
    fingerprint: str,
    run_id: str,
    *,
    qa_state: str = "passed",
    created_at: str,
) -> None:
    connection.execute(
        "INSERT INTO summary_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            summary_id,
            revision_id,
            WORK,
            "snapshot:" + summary_id.split(":", 1)[1],
            fingerprint,
            run_id,
            "model_output:" + summary_id.split(":", 1)[1],
            "zh-TW",
            "plain-zh-TW-v1",
            qa_state,
            created_at,
        ),
    )


def _setup(tmp_path: Path):
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE paper_revisions(
          id TEXT PRIMARY KEY,work_id TEXT NOT NULL,source_updated_at TEXT,observed_at TEXT NOT NULL);
        CREATE TABLE model_runs(
          id TEXT PRIMARY KEY,state TEXT NOT NULL,input_fingerprint TEXT NOT NULL,started_at TEXT NOT NULL);
        CREATE TABLE summary_revisions(
          id TEXT PRIMARY KEY,revision_id TEXT NOT NULL,work_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,
          generation_fingerprint TEXT NOT NULL,generation_run_id TEXT NOT NULL,output_object_id TEXT NOT NULL,
          language TEXT NOT NULL,explanation_profile TEXT NOT NULL,qa_state TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE current_summaries(
          work_id TEXT NOT NULL,language TEXT NOT NULL,explanation_profile TEXT NOT NULL,
          summary_id TEXT NOT NULL,revision_id TEXT NOT NULL,expected_input_fingerprint TEXT NOT NULL,
          pointer_version INTEGER NOT NULL,PRIMARY KEY(work_id,language,explanation_profile));
        """
    )
    connection.executemany(
        "INSERT INTO paper_revisions VALUES(?,?,?,?)",
        [
            (R1, WORK, "2026-09-20T00:00:00+00:00", "2026-09-20T01:00:00+00:00"),
            (R2, WORK, "2026-09-21T00:00:00+00:00", "2026-09-21T01:00:00+00:00"),
        ],
    )
    _insert_run(connection, RUN1, F1, "2026-09-20T01:30:00+00:00")
    _insert_run(connection, RUN_BAD, F_BAD, "2026-09-21T01:40:00+00:00")
    _insert_summary(
        connection,
        S1,
        R1,
        F1,
        RUN1,
        created_at="2026-09-20T02:00:00+00:00",
    )
    _insert_summary(
        connection,
        BAD,
        R2,
        F_BAD,
        RUN_BAD,
        qa_state="rejected",
        created_at="2026-09-21T03:00:00+00:00",
    )
    connection.commit()
    connection.close()
    return path, PublishCurrentSummary(SqliteCurrentSummaryStoreAdapter(_connect(path)))


def _add_newer_revision(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        _insert_run(connection, RUN2, F2, "2026-09-21T01:30:00+00:00")
        _insert_summary(
            connection,
            S2,
            R2,
            F2,
            RUN2,
            created_at="2026-09-21T02:00:00+00:00",
        )


def _add_newer_generation_same_revision(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        _insert_run(connection, RUN1_NEW, F1_NEW, "2026-09-20T01:45:00+00:00")
        _insert_summary(
            connection,
            S1_NEW,
            R1,
            F1_NEW,
            RUN1_NEW,
            created_at="2026-09-20T02:10:00+00:00",
        )


def test_initial_publish_replay_and_newer_revision(tmp_path: Path):
    path, publish = _setup(tmp_path)
    first = publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)
    assert first.pointer_version == 1
    assert publish(S1, expected_input_fingerprint=F1, expected_pointer_version=1) == first
    _add_newer_revision(path)
    second = publish(S2, expected_input_fingerprint=F2, expected_pointer_version=1)
    assert second.pointer_version == 2 and second.revision_id == R2


def test_rejected_wrong_input_and_old_revision_are_blocked(tmp_path: Path):
    path, publish = _setup(tmp_path)
    with pytest.raises(ExplanationVerificationError, match="summary_not_verified"):
        publish(BAD, expected_input_fingerprint=F_BAD, expected_pointer_version=None)
    with pytest.raises(ExplanationVerificationError, match="summary_input_mismatch"):
        publish(S1, expected_input_fingerprint="0" * 64, expected_pointer_version=None)
    _add_newer_revision(path)
    publish(S2, expected_input_fingerprint=F2, expected_pointer_version=None)
    with pytest.raises(ExplanationVerificationError, match="stale_summary"):
        publish(S1, expected_input_fingerprint=F1, expected_pointer_version=1)


def test_known_newer_passed_summary_blocks_old_initial_publish(tmp_path: Path):
    path, publish = _setup(tmp_path)
    _add_newer_revision(path)
    with pytest.raises(ExplanationVerificationError, match="stale_summary"):
        publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)
    assert publish(S2, expected_input_fingerprint=F2, expected_pointer_version=None).pointer_version == 1


def test_newer_generation_of_same_revision_blocks_older_generation(tmp_path: Path):
    path, publish = _setup(tmp_path)
    _add_newer_generation_same_revision(path)
    with pytest.raises(ExplanationVerificationError, match="stale_summary"):
        publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)
    current = publish(S1_NEW, expected_input_fingerprint=F1_NEW, expected_pointer_version=None)
    assert current.summary_id == S1_NEW and current.revision_id == R1


def test_generation_run_must_be_succeeded_and_match_summary_fingerprint(tmp_path: Path):
    path, publish = _setup(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE model_runs SET state='failed' WHERE id=?", (RUN1,))
    with pytest.raises(ExplanationVerificationError, match="current_summary_corrupt"):
        publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE model_runs SET state='succeeded',input_fingerprint=? WHERE id=?",
            ("0" * 64, RUN1),
        )
    with pytest.raises(ExplanationVerificationError, match="current_summary_corrupt"):
        publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)


def test_stale_cas_cannot_overwrite_newer_pointer(tmp_path: Path):
    path, publish = _setup(tmp_path)
    publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)
    _add_newer_revision(path)
    publish(S2, expected_input_fingerprint=F2, expected_pointer_version=1)
    with pytest.raises(ExplanationVerificationError, match="current_summary_conflict"):
        publish(S1, expected_input_fingerprint=F1, expected_pointer_version=1)


def test_concurrent_publishers_with_same_expected_version_only_one_wins(tmp_path: Path):
    path, publish = _setup(tmp_path)
    publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)
    _add_newer_revision(path)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker():
        command = PublishCurrentSummary(SqliteCurrentSummaryStoreAdapter(_connect(path)))
        try:
            command(S2, expected_input_fingerprint=F2, expected_pointer_version=1)
            value = "ok"
        except ExplanationVerificationError as exc:
            value = exc.code
        with lock:
            outcomes.append(value)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("current_summary_conflict") == 1


def test_owner_can_read_pointer_and_publish_verified_persisted_summary(tmp_path: Path):
    path, _ = _setup(tmp_path)
    store = SqliteCurrentSummaryStoreAdapter(_connect(path))
    assert store.read(WORK, "zh-TW", "plain-zh-TW-v1") is None

    persisted = PersistedExplanation(
        S1,
        "snapshot:" + S1.split(":", 1)[1],
        R1,
        WORK,
        F1,
        RUN1,
        "model_output:" + S1.split(":", 1)[1],
        "passed",
        "zh-TW",
        "plain-zh-TW-v1",
        True,
    )
    first = PublishVerifiedCurrentSummary(store)(persisted)
    replay = PublishVerifiedCurrentSummary(store)(persisted)

    assert first == replay
    assert store.read(WORK, "zh-TW", "plain-zh-TW-v1") == first
