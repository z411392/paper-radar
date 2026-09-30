import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.watch_profiles.domain.services.relevance_persistence_rules import (
    RelevancePersistenceRules,
)
from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment
from libs.watch_profiles.dtos.relevance_persistence import PersistedRelevanceAssessment
from libs.watch_profiles.exceptions.relevance_persistence_error import RelevancePersistenceError


class SqliteRelevanceAssessmentStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _time(value: object) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise RelevancePersistenceError("invalid_relevance_time")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise RelevancePersistenceError("invalid_relevance_time") from None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.in_transaction:
                raise RelevancePersistenceError("owned_connection_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except RelevancePersistenceError:
            raise
        except sqlite3.IntegrityError as exc:
            raise RelevancePersistenceError("relevance_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "relevance_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "relevance_database_error"
            )
            raise RelevancePersistenceError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _validate_relations(
        connection: sqlite3.Connection,
        assessment: RelevanceAssessment,
    ) -> str:
        profile = connection.execute(
            "SELECT lifecycle,published_revision FROM watch_profiles WHERE id=?",
            (assessment.profile_id,),
        ).fetchone()
        if profile is None:
            raise RelevancePersistenceError("relevance_profile_missing")
        selected = connection.execute(
            "SELECT 1 FROM watch_profile_domains "
            "WHERE profile_id=? AND revision=? AND domain_id=? AND domain_revision=?",
            (
                assessment.profile_id,
                assessment.profile_revision,
                assessment.domain_id,
                assessment.domain_revision,
            ),
        ).fetchone()
        if selected is None:
            raise RelevancePersistenceError("relevance_domain_mismatch")
        if assessment.execution_state != "stale" and (
            profile["lifecycle"] != "active"
            or profile["published_revision"] != assessment.profile_revision
        ):
            raise RelevancePersistenceError("relevance_not_current")

        snapshot = connection.execute(
            "SELECT revision_id FROM evidence_snapshots WHERE id=?",
            (assessment.snapshot_id,),
        ).fetchone()
        if snapshot is None:
            raise RelevancePersistenceError("relevance_snapshot_missing")
        if assessment.anchor_ids:
            placeholders = ",".join("?" for _ in assessment.anchor_ids)
            rows = connection.execute(
                "SELECT id FROM evidence_anchors WHERE snapshot_id=? "
                f"AND id IN ({placeholders})",
                (assessment.snapshot_id, *assessment.anchor_ids),
            ).fetchall()
            if {row["id"] for row in rows} != set(assessment.anchor_ids):
                raise RelevancePersistenceError("relevance_anchor_mismatch")
        return snapshot["revision_id"]

    @staticmethod
    def _expected(
        assessment: RelevanceAssessment,
        revision_id: str,
        reason_json: str,
    ) -> tuple[object, ...]:
        return (
            assessment.profile_id,
            assessment.profile_revision,
            revision_id,
            assessment.input_fingerprint,
            assessment.decision,
            assessment.execution_state,
            reason_json,
        )

    @staticmethod
    def _domain_expected(assessment: RelevanceAssessment) -> tuple[object, ...]:
        return (
            assessment.domain_id,
            assessment.domain_revision,
            assessment.snapshot_id,
        )

    def persist(
        self,
        assessment: RelevanceAssessment,
        *,
        assessed_at: datetime,
    ) -> PersistedRelevanceAssessment:
        prepared = RelevancePersistenceRules.prepare(assessment)
        time_text = self._time(assessed_at)
        with self._transaction() as connection:
            revision_id = self._validate_relations(connection, assessment)
            expected = self._expected(assessment, revision_id, prepared.reason_json)
            row = connection.execute(
                "SELECT profile_id,profile_revision,revision_id,input_fingerprint,"
                "decision,execution_state,reason_json,assessed_at "
                "FROM relevance_assessments WHERE id=?",
                (prepared.assessment_id,),
            ).fetchone()
            domain = connection.execute(
                "SELECT domain_id,domain_revision,snapshot_id "
                "FROM relevance_assessment_domains WHERE assessment_id=?",
                (prepared.assessment_id,),
            ).fetchone()

            if row is None:
                if domain is not None:
                    raise RelevancePersistenceError("relevance_database_corrupt")
                connection.execute(
                    "INSERT INTO relevance_assessments("
                    "id,profile_id,profile_revision,revision_id,input_fingerprint,"
                    "decision,execution_state,reason_json,assessed_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        prepared.assessment_id,
                        assessment.profile_id,
                        assessment.profile_revision,
                        revision_id,
                        assessment.input_fingerprint,
                        assessment.decision,
                        assessment.execution_state,
                        prepared.reason_json,
                        time_text,
                    ),
                )
                connection.execute(
                    "INSERT INTO relevance_assessment_domains("
                    "assessment_id,domain_id,domain_revision,snapshot_id"
                    ") VALUES(?,?,?,?)",
                    (prepared.assessment_id, *self._domain_expected(assessment)),
                )
                return PersistedRelevanceAssessment(
                    prepared.assessment_id,
                    assessment.execution_state,
                    assessment.decision,
                    False,
                )

            if domain is None or tuple(domain) != self._domain_expected(assessment):
                raise RelevancePersistenceError("relevance_result_conflict")
            if tuple(row[:4]) != expected[:4]:
                raise RelevancePersistenceError("relevance_result_conflict")
            if tuple(row[:7]) == expected:
                return PersistedRelevanceAssessment(
                    prepared.assessment_id,
                    assessment.execution_state,
                    assessment.decision,
                    True,
                )

            current_state = row["execution_state"]
            if current_state in {"succeeded", "stale"}:
                raise RelevancePersistenceError("relevance_result_conflict")
            if current_state not in {"pending", "failed"}:
                raise RelevancePersistenceError("relevance_database_corrupt")
            if assessment.execution_state not in {"failed", "stale", "succeeded"}:
                raise RelevancePersistenceError("relevance_result_conflict")
            if time_text <= row["assessed_at"]:
                raise RelevancePersistenceError("stale_relevance_result")

            changed = connection.execute(
                "UPDATE relevance_assessments SET decision=?,execution_state=?,"
                "reason_json=?,assessed_at=? WHERE id=? AND execution_state=?",
                (
                    assessment.decision,
                    assessment.execution_state,
                    prepared.reason_json,
                    time_text,
                    prepared.assessment_id,
                    current_state,
                ),
            ).rowcount
            if changed != 1:
                raise RelevancePersistenceError("relevance_database_conflict")
            return PersistedRelevanceAssessment(
                prepared.assessment_id,
                assessment.execution_state,
                assessment.decision,
                False,
            )
