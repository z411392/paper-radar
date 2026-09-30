import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.paper_explanations.domain.services.verification_report_rules import VerificationReportRules
from libs.paper_explanations.dtos.explanation_verification import ExplanationVerificationResult
from libs.paper_explanations.dtos.verification_persistence_result import VerificationPersistenceResult
from libs.paper_explanations.exceptions.explanation_verification_error import ExplanationVerificationError


class SqliteExplanationVerificationStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _time(value: datetime) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ExplanationVerificationError("invalid_verification_time")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError) as exc:
            raise ExplanationVerificationError("invalid_verification_time") from exc

    @staticmethod
    def _id(value: object, prefix: str, code: str) -> str:
        if not isinstance(value, str) or re.fullmatch(prefix + r":[0-9a-f]{64}", value) is None:
            raise ExplanationVerificationError(code)
        return value

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise ExplanationVerificationError("owned_connection_required")
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise ExplanationVerificationError("foreign_keys_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except sqlite3.IntegrityError as exc:
            raise ExplanationVerificationError("verification_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "verification_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "verification_database_error"
            )
            raise ExplanationVerificationError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _verdicts(result: ExplanationVerificationResult) -> tuple[str, str | None]:
        deterministic = result.deterministic.verdict
        if result.support_execution_state == "not_run":
            support = None
        elif result.support_execution_state == "failed":
            support = "failed"
        else:
            assert result.support is not None
            values = {item.verdict for item in result.support.statements}
            support = (
                "rejected"
                if "unsupported" in values
                else "failed"
                if "uncertain" in values
                else "passed"
            )
        return deterministic, support

    @staticmethod
    def _verification_id(summary_id: str, kind: str, report_object_id: str) -> str:
        raw = f"{summary_id}\0{kind}\0{report_object_id}".encode("utf-8")
        return "verification:" + hashlib.sha256(raw).hexdigest()

    def record(
        self,
        summary_id: str,
        result: ExplanationVerificationResult,
        report_object_id: str,
        verified_at: datetime,
    ) -> VerificationPersistenceResult:
        VerificationReportRules.validate(result)
        summary_id = self._id(summary_id, "summary", "invalid_summary_id")
        if not isinstance(report_object_id, str) or re.fullmatch(
            r"(?:evidence|model_output):[0-9a-f]{64}", report_object_id
        ) is None:
            raise ExplanationVerificationError("invalid_verification_report")
        checked_at = self._time(verified_at)
        deterministic_verdict, support_verdict = self._verdicts(result)
        deterministic_id = self._verification_id(summary_id, "deterministic", report_object_id)
        support_id = (
            None
            if support_verdict is None
            else self._verification_id(summary_id, "supportiveness", report_object_id)
        )

        with self._transaction() as connection:
            summary = connection.execute(
                "SELECT id,revision_id,work_id,snapshot_id,generation_fingerprint,qa_state "
                "FROM summary_revisions WHERE id=?",
                (summary_id,),
            ).fetchone()
            if summary is None:
                raise ExplanationVerificationError("summary_missing")
            deterministic = result.deterministic
            if (
                summary["snapshot_id"] != deterministic.snapshot_id
                or summary["revision_id"] != deterministic.revision_id
                or summary["work_id"] != deterministic.work_id
                or summary["generation_fingerprint"] != deterministic.input_fingerprint
            ):
                raise ExplanationVerificationError("verification_summary_mismatch")
            if connection.execute(
                "SELECT 1 FROM object_registry WHERE object_id=? AND state='available'",
                (report_object_id,),
            ).fetchone() is None:
                raise ExplanationVerificationError("verification_report_missing")

            latest = connection.execute(
                "SELECT verified_at FROM verification_results WHERE summary_id=? "
                "ORDER BY verified_at DESC,id DESC LIMIT 1",
                (summary_id,),
            ).fetchone()
            if latest is not None and latest["verified_at"] > checked_at:
                raise ExplanationVerificationError("stale_verification")

            rows = [(deterministic_id, "deterministic", deterministic_verdict)]
            if support_id is not None and support_verdict is not None:
                rows.append((support_id, "supportiveness", support_verdict))
            for verification_id, kind, verdict in rows:
                existing = connection.execute(
                    "SELECT report_object_id,verdict,verified_at FROM verification_results WHERE id=?",
                    (verification_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["report_object_id"] != report_object_id
                        or existing["verdict"] != verdict
                        or existing["verified_at"] != checked_at
                    ):
                        raise ExplanationVerificationError("verification_conflict")
                    continue
                connection.execute(
                    "INSERT INTO verification_results("
                    "id,summary_id,verifier_kind,run_id,report_object_id,verdict,verified_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (verification_id, summary_id, kind, None, report_object_id, verdict, checked_at),
                )

            if (
                latest is not None
                and latest["verified_at"] == checked_at
                and summary["qa_state"] != result.qa_state
            ):
                raise ExplanationVerificationError("verification_conflict")
            connection.execute(
                "UPDATE summary_revisions SET qa_state=? WHERE id=?",
                (result.qa_state, summary_id),
            )
            if result.qa_state != "passed":
                connection.execute(
                    "DELETE FROM current_summaries WHERE summary_id=?",
                    (summary_id,),
                )
            return VerificationPersistenceResult(
                summary_id,
                result.qa_state,
                report_object_id,
                deterministic_id,
                support_id,
                checked_at,
            )
