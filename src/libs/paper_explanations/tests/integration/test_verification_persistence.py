import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.paper_explanations.adapters.driven.sqlite_explanation_verification_store_adapter import (
    SqliteExplanationVerificationStoreAdapter,
)
from libs.paper_explanations.application.commands.record_explanation_verification import (
    RecordExplanationVerification,
)
from libs.paper_explanations.dtos.explanation_verification import (
    DeterministicVerificationReport,
    ExplanationVerificationResult,
    SupportStatementVerdict,
    SupportVerificationResult,
    VerificationFinding,
)
from libs.paper_explanations.exceptions.explanation_verification_error import ExplanationVerificationError

AT = datetime(2026, 9, 23, tzinfo=timezone.utc)
SUMMARY = "summary:" + "1" * 64
SNAPSHOT = "snapshot:" + "2" * 64
REVISION = "revision:" + "3" * 64
WORK = "work:" + "4" * 64
FINGERPRINT = "5" * 64
CLAIM = "claim:" + "6" * 64


class Reports:
    def __init__(self, path: Path) -> None:
        self.path = path

    def publish(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        object_id = "evidence:" + digest
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT OR IGNORE INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
            (
                object_id,
                digest,
                f"objects/evidence/{digest[:2]}/{digest}",
                "evidence",
                "application/json",
                len(content),
                "available",
                AT.isoformat(),
                "verification-report-v1",
            ),
        )
        connection.commit()
        connection.close()
        return object_id


def connect(path: Path):
    def factory():
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    return factory


def setup(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "db.sqlite3"
    connection = sqlite3.connect(path)
    root = Path(__file__).resolve().parents[5]
    for name in (
        "0001-object-registry.sql",
        "0002-watch-profiles.sql",
        "0003-scholarly-catalog.sql",
        "0005-paper-explanations.sql",
    ):
        connection.executescript((root / "migrations" / name).read_text())
    connection.execute(
        "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "model_output:" + "7" * 64,
            "7" * 64,
            "objects/model_output/77/" + "7" * 64,
            "model_output",
            "application/json",
            2,
            "available",
            AT.isoformat(),
            "model-output-v1",
        ),
    )
    connection.execute(
        "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
        (WORK, "title", "preprint", None, None, AT.isoformat(), AT.isoformat()),
    )
    manifestation = "manifestation:" + "8" * 64
    connection.execute(
        "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
        (manifestation, WORK, "arxiv", "2609.1", "preprint", "https://arxiv.org", AT.isoformat()),
    )
    connection.execute(
        "INSERT INTO paper_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            REVISION,
            manifestation,
            WORK,
            "1",
            "9" * 64,
            "title",
            None,
            AT.isoformat(),
            None,
            None,
            AT.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            SNAPSHOT,
            REVISION,
            WORK,
            "model_output:" + "7" * 64,
            "model_output:" + "7" * 64,
            "p",
            "abstract_only",
            "{}",
            "a" * 64,
            AT.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO model_runs("
        "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,state,started_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            "run:x",
            "reading",
            "openrouter",
            "google/gemini-3.8-flash",
            "b" * 64,
            "c" * 64,
            "succeeded",
            AT.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO summary_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            SUMMARY,
            REVISION,
            WORK,
            SNAPSHOT,
            FINGERPRINT,
            "run:x",
            "model_output:" + "7" * 64,
            "zh-TW",
            "plain-zh-TW-v1",
            "pending",
            AT.isoformat(),
        ),
    )
    connection.commit()
    connection.close()
    command = RecordExplanationVerification(
        Reports(path),
        SqliteExplanationVerificationStoreAdapter(connect(path)),
    )
    return path, command


def result(qa_state="passed", det="passed", support="supported"):
    findings = (
        ()
        if det == "passed"
        else (VerificationFinding("number_mismatch", "card", 0, (CLAIM,)),)
    )
    deterministic = DeterministicVerificationReport(
        SNAPSHOT, REVISION, WORK, FINGERPRINT, det, findings
    )
    if det == "rejected":
        return ExplanationVerificationResult(deterministic, None, "rejected", "not_run")
    if support == "failed":
        return ExplanationVerificationResult(deterministic, None, "pending", "failed")
    support_result = SupportVerificationResult(
        SNAPSHOT,
        "d" * 64,
        (SupportStatementVerdict(0, support, (CLAIM,)),),
    )
    return ExplanationVerificationResult(deterministic, support_result, qa_state, "succeeded")


def test_passed_verification_updates_summary_and_persists_two_reports(tmp_path: Path):
    path, command = setup(tmp_path)
    saved = command(SUMMARY, result(), verified_at=AT + timedelta(minutes=1))
    assert saved.qa_state == "passed"
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT qa_state FROM summary_revisions").fetchone()[0] == "passed"
    rows = connection.execute(
        "SELECT verifier_kind,verdict FROM verification_results ORDER BY verifier_kind"
    ).fetchall()
    assert rows == [("deterministic", "passed"), ("supportiveness", "passed")]
    connection.close()


def test_rejected_and_failed_support_map_to_rejected_or_pending(tmp_path: Path):
    path, command = setup(tmp_path)
    command(
        SUMMARY,
        result(qa_state="rejected", det="rejected"),
        verified_at=AT + timedelta(minutes=1),
    )
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT qa_state FROM summary_revisions").fetchone()[0] == "rejected"
    connection.close()

    path2, command2 = setup(tmp_path / "second")
    command2(
        SUMMARY,
        result(qa_state="pending", support="failed"),
        verified_at=AT + timedelta(minutes=1),
    )
    connection = sqlite3.connect(path2)
    assert connection.execute("SELECT qa_state FROM summary_revisions").fetchone()[0] == "pending"
    support_verdict = connection.execute(
        "SELECT verdict FROM verification_results WHERE verifier_kind='supportiveness'"
    ).fetchone()[0]
    assert support_verdict == "failed"
    connection.close()


def test_stale_verification_cannot_overwrite_newer_state(tmp_path: Path):
    path, command = setup(tmp_path)
    command(SUMMARY, result(), verified_at=AT + timedelta(minutes=2))
    with pytest.raises(ExplanationVerificationError, match="stale_verification"):
        command(
            SUMMARY,
            result(qa_state="rejected", det="rejected"),
            verified_at=AT + timedelta(minutes=1),
        )
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT qa_state FROM summary_revisions").fetchone()[0] == "passed"
    connection.close()


def test_summary_identity_mismatch_is_rejected(tmp_path: Path):
    _, command = setup(tmp_path)
    bad_det = DeterministicVerificationReport(
        SNAPSHOT,
        REVISION,
        WORK,
        "e" * 64,
        "passed",
        (),
    )
    bad = ExplanationVerificationResult(
        bad_det,
        SupportVerificationResult(
            SNAPSHOT,
            "d" * 64,
            (SupportStatementVerdict(0, "supported", (CLAIM,)),),
        ),
        "passed",
        "succeeded",
    )
    with pytest.raises(ExplanationVerificationError, match="verification_summary_mismatch"):
        command(SUMMARY, bad, verified_at=AT + timedelta(minutes=1))


def test_new_rejection_invalidates_existing_current_pointer(tmp_path: Path):
    path, command = setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE summary_revisions SET qa_state='passed' WHERE id=?",
        (SUMMARY,),
    )
    connection.execute(
        "INSERT INTO current_summaries VALUES(?,?,?,?,?,?,?)",
        (WORK, "zh-TW", "plain-zh-TW-v1", SUMMARY, REVISION, FINGERPRINT, 1),
    )
    connection.commit()
    connection.close()

    command(
        SUMMARY,
        result(qa_state="rejected", det="rejected"),
        verified_at=AT + timedelta(minutes=2),
    )
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT qa_state FROM summary_revisions").fetchone()[0] == "rejected"
    assert connection.execute("SELECT COUNT(*) FROM current_summaries").fetchone()[0] == 0
    connection.close()
