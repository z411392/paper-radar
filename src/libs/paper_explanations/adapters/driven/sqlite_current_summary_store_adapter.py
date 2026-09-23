import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.paper_explanations.dtos.current_summary_pointer import CurrentSummaryPointer
from libs.paper_explanations.exceptions.explanation_verification_error import ExplanationVerificationError


class SqliteCurrentSummaryStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _id(value: object, prefix: str, code: str) -> str:
        if not isinstance(value, str) or re.fullmatch(prefix + r":[0-9a-f]{64}", value) is None:
            raise ExplanationVerificationError(code)
        return value

    @staticmethod
    def _fingerprint(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ExplanationVerificationError("invalid_current_input")
        return value

    @staticmethod
    def _instant(value: object) -> datetime:
        if not isinstance(value, str):
            raise ExplanationVerificationError("current_summary_corrupt")
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError) as exc:
            raise ExplanationVerificationError("current_summary_corrupt") from exc
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ExplanationVerificationError("current_summary_corrupt")
        return moment.astimezone(timezone.utc)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.in_transaction:
                raise ExplanationVerificationError("owned_connection_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except sqlite3.IntegrityError as exc:
            raise ExplanationVerificationError("current_summary_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "current_summary_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "current_summary_database_error"
            )
            raise ExplanationVerificationError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _summary(connection: sqlite3.Connection, summary_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT s.*,r.source_updated_at,r.observed_at,"
            "m.state AS generation_state,m.input_fingerprint AS generation_input_fingerprint,"
            "m.started_at AS generation_started_at "
            "FROM summary_revisions s "
            "JOIN paper_revisions r ON r.id=s.revision_id AND r.work_id=s.work_id "
            "JOIN model_runs m ON m.id=s.generation_run_id "
            "WHERE s.id=?",
            (summary_id,),
        ).fetchone()
        if row is None:
            raise ExplanationVerificationError("summary_missing")
        if (
            row["generation_state"] != "succeeded"
            or row["generation_input_fingerprint"] != row["generation_fingerprint"]
        ):
            raise ExplanationVerificationError("current_summary_corrupt")
        return row

    @classmethod
    def _freshness(cls, row: sqlite3.Row) -> tuple[datetime, datetime, datetime]:
        observed = cls._instant(row["observed_at"])
        primary = cls._instant(row["source_updated_at"]) if row["source_updated_at"] else observed
        generation_started = cls._instant(row["generation_started_at"])
        cls._id(row["revision_id"], "revision", "current_summary_corrupt")
        return primary, observed, generation_started

    @classmethod
    def _reject_if_newer_passed_exists(
        cls,
        connection: sqlite3.Connection,
        candidate: sqlite3.Row,
    ) -> None:
        rows = connection.execute(
            "SELECT id FROM summary_revisions "
            "WHERE work_id=? AND language=? AND explanation_profile=? "
            "AND qa_state='passed' AND id<>?",
            (
                candidate["work_id"],
                candidate["language"],
                candidate["explanation_profile"],
                candidate["id"],
            ),
        ).fetchall()
        candidate_freshness = cls._freshness(candidate)
        for row in rows:
            other = cls._summary(connection, row["id"])
            if cls._freshness(other) >= candidate_freshness:
                raise ExplanationVerificationError("stale_summary")

    @staticmethod
    def _pointer(row: sqlite3.Row) -> CurrentSummaryPointer:
        return CurrentSummaryPointer(
            row["work_id"],
            row["language"],
            row["explanation_profile"],
            row["summary_id"],
            row["revision_id"],
            row["expected_input_fingerprint"],
            row["pointer_version"],
        )

    def publish(
        self,
        summary_id: str,
        expected_input_fingerprint: str,
        expected_pointer_version: int | None,
    ) -> CurrentSummaryPointer:
        summary_id = self._id(summary_id, "summary", "invalid_summary_id")
        fingerprint = self._fingerprint(expected_input_fingerprint)
        if expected_pointer_version is not None and (
            type(expected_pointer_version) is not int or expected_pointer_version < 1
        ):
            raise ExplanationVerificationError("invalid_pointer_version")

        with self._transaction() as connection:
            candidate = self._summary(connection, summary_id)
            if candidate["qa_state"] != "passed":
                raise ExplanationVerificationError("summary_not_verified")
            if candidate["generation_fingerprint"] != fingerprint:
                raise ExplanationVerificationError("summary_input_mismatch")
            self._reject_if_newer_passed_exists(connection, candidate)

            current = connection.execute(
                "SELECT * FROM current_summaries "
                "WHERE work_id=? AND language=? AND explanation_profile=?",
                (candidate["work_id"], candidate["language"], candidate["explanation_profile"]),
            ).fetchone()
            if current is None:
                if expected_pointer_version is not None:
                    raise ExplanationVerificationError("current_summary_conflict")
                connection.execute(
                    "INSERT INTO current_summaries("
                    "work_id,language,explanation_profile,summary_id,revision_id,"
                    "expected_input_fingerprint,pointer_version) VALUES(?,?,?,?,?,?,1)",
                    (
                        candidate["work_id"],
                        candidate["language"],
                        candidate["explanation_profile"],
                        summary_id,
                        candidate["revision_id"],
                        fingerprint,
                    ),
                )
            else:
                if expected_pointer_version != current["pointer_version"]:
                    raise ExplanationVerificationError("current_summary_conflict")
                if current["summary_id"] == summary_id:
                    if (
                        current["revision_id"] != candidate["revision_id"]
                        or current["expected_input_fingerprint"] != fingerprint
                    ):
                        raise ExplanationVerificationError("current_summary_corrupt")
                    return self._pointer(current)
                current_summary = self._summary(connection, current["summary_id"])
                if self._freshness(candidate) <= self._freshness(current_summary):
                    raise ExplanationVerificationError("stale_summary")
                next_version = current["pointer_version"] + 1
                changed = connection.execute(
                    "UPDATE current_summaries SET summary_id=?,revision_id=?,"
                    "expected_input_fingerprint=?,pointer_version=? "
                    "WHERE work_id=? AND language=? AND explanation_profile=? AND pointer_version=?",
                    (
                        summary_id,
                        candidate["revision_id"],
                        fingerprint,
                        next_version,
                        candidate["work_id"],
                        candidate["language"],
                        candidate["explanation_profile"],
                        current["pointer_version"],
                    ),
                ).rowcount
                if changed != 1:
                    raise ExplanationVerificationError("current_summary_conflict")

            saved = connection.execute(
                "SELECT * FROM current_summaries "
                "WHERE work_id=? AND language=? AND explanation_profile=?",
                (candidate["work_id"], candidate["language"], candidate["explanation_profile"]),
            ).fetchone()
            if saved is None:
                raise ExplanationVerificationError("current_summary_corrupt")
            return self._pointer(saved)
