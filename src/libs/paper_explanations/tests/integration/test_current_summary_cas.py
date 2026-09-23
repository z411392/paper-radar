import sqlite3
import threading
from pathlib import Path

import pytest

from libs.paper_explanations.adapters.driven.sqlite_current_summary_store_adapter import (
    SqliteCurrentSummaryStoreAdapter,
)
from libs.paper_explanations.application.commands.publish_current_summary import PublishCurrentSummary
from libs.paper_explanations.exceptions.explanation_verification_error import ExplanationVerificationError

WORK = "work:" + "1" * 64
R1 = "revision:" + "2" * 64
R2 = "revision:" + "3" * 64
S1 = "summary:" + "4" * 64
S2 = "summary:" + "5" * 64
BAD = "summary:" + "6" * 64
F1 = "a" * 64
F2 = "b" * 64


def _connect(path: Path):
    def factory():
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE paper_revisions(
          id TEXT PRIMARY KEY,work_id TEXT NOT NULL,source_updated_at TEXT,observed_at TEXT NOT NULL);
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
    common = ("run:x", "model_output:x", "zh-TW", "plain-zh-TW-v1")
    connection.executemany(
        "INSERT INTO summary_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [
            (S1, R1, WORK, "snap:1", F1, *common, "passed", "2026-09-20T02:00:00+00:00"),
            (S2, R2, WORK, "snap:2", F2, *common, "passed", "2026-09-21T02:00:00+00:00"),
            (BAD, R2, WORK, "snap:3", "c" * 64, *common, "rejected", "2026-09-21T03:00:00+00:00"),
        ],
    )
    connection.commit()
    connection.close()
    return path, PublishCurrentSummary(SqliteCurrentSummaryStoreAdapter(_connect(path)))


def test_initial_publish_replay_and_newer_revision(tmp_path: Path):
    _, publish = _setup(tmp_path)
    first = publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)
    assert first.pointer_version == 1
    assert publish(S1, expected_input_fingerprint=F1, expected_pointer_version=1) == first
    second = publish(S2, expected_input_fingerprint=F2, expected_pointer_version=1)
    assert second.pointer_version == 2 and second.revision_id == R2


def test_rejected_wrong_input_and_old_revision_are_blocked(tmp_path: Path):
    _, publish = _setup(tmp_path)
    with pytest.raises(ExplanationVerificationError, match="summary_not_verified"):
        publish(BAD, expected_input_fingerprint="c" * 64, expected_pointer_version=None)
    with pytest.raises(ExplanationVerificationError, match="summary_input_mismatch"):
        publish(S1, expected_input_fingerprint="d" * 64, expected_pointer_version=None)
    publish(S2, expected_input_fingerprint=F2, expected_pointer_version=None)
    with pytest.raises(ExplanationVerificationError, match="stale_summary"):
        publish(S1, expected_input_fingerprint=F1, expected_pointer_version=1)


def test_stale_cas_cannot_overwrite_newer_pointer(tmp_path: Path):
    _, publish = _setup(tmp_path)
    publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)
    publish(S2, expected_input_fingerprint=F2, expected_pointer_version=1)
    with pytest.raises(ExplanationVerificationError, match="current_summary_conflict"):
        publish(S1, expected_input_fingerprint=F1, expected_pointer_version=1)


def test_concurrent_publishers_with_same_expected_version_only_one_wins(tmp_path: Path):
    path, publish = _setup(tmp_path)
    publish(S1, expected_input_fingerprint=F1, expected_pointer_version=None)
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
