import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.scholarly_catalog.dtos.access_assessment import AccessAssessment
from libs.scholarly_catalog.dtos.manifestation_access_identity import ManifestationAccessIdentity
from libs.scholarly_catalog.exceptions.access_assessment_error import AccessAssessmentError


class SqliteAccessAssessmentStoreAdapter:
    _READER_ACCESS = {"free", "restricted", "unknown"}
    _AUTOMATED = {"permitted", "prohibited", "unknown"}
    _SCOPES = {"abstract", "full_text"}

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _identifier(value: object, code: str) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,127}", value) is None
        ):
            raise AccessAssessmentError(code)
        return value

    @staticmethod
    def _json(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise AccessAssessmentError("invalid_access_assessment") from exc

    @classmethod
    def _canonical_json(cls, value: str, code: str) -> object:
        if not isinstance(value, str):
            raise AccessAssessmentError(code)
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise AccessAssessmentError(code) from exc
        if cls._json(decoded) != value:
            raise AccessAssessmentError(code)
        return decoded

    @staticmethod
    def _instant(value: str) -> datetime:
        if not isinstance(value, str):
            raise AccessAssessmentError("invalid_access_time")
        try:
            moment = datetime.fromisoformat(value)
        except (ValueError, OverflowError) as exc:
            raise AccessAssessmentError("invalid_access_time") from exc
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise AccessAssessmentError("invalid_access_time")
        canonical = moment.astimezone(timezone.utc)
        if canonical.isoformat() != value:
            raise AccessAssessmentError("invalid_access_time")
        return canonical

    @staticmethod
    def _query_time(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise AccessAssessmentError("invalid_access_time")
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError) as exc:
            raise AccessAssessmentError("invalid_access_time") from exc

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            if connection.in_transaction:
                raise AccessAssessmentError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise AccessAssessmentError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with connection:
                yield connection
        except sqlite3.IntegrityError as exc:
            raise AccessAssessmentError("access_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "access_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "access_database_error"
            )
            raise AccessAssessmentError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _validate(cls, assessment: AccessAssessment) -> tuple[str, str]:
        if not isinstance(assessment, AccessAssessment):
            raise AccessAssessmentError("invalid_access_assessment")
        if re.fullmatch(r"access:[0-9a-f]{64}", assessment.assessment_id) is None:
            raise AccessAssessmentError("invalid_access_assessment")
        cls._identifier(assessment.manifestation_id, "invalid_access_assessment")
        if (
            assessment.content_scope not in cls._SCOPES
            or assessment.reader_access not in cls._READER_ACCESS
            or assessment.automated_retrieval not in cls._AUTOMATED
            or not isinstance(assessment.location_url, str)
            or not assessment.location_url
            or len(assessment.location_url) > 8192
            or not isinstance(assessment.permitted_uses, tuple)
            or tuple(sorted(set(assessment.permitted_uses))) != assessment.permitted_uses
        ):
            raise AccessAssessmentError("invalid_access_assessment")
        uses_json = cls._json(list(assessment.permitted_uses))
        evidence = cls._canonical_json(assessment.evidence_json, "invalid_access_assessment")
        if (
            not isinstance(evidence, dict)
            or evidence.get("content_scope") != assessment.content_scope
            or assessment.license_id is not None
            and (not isinstance(assessment.license_id, str) or not assessment.license_id)
            or assessment.content_version_binding is not None
            and (
                not isinstance(assessment.content_version_binding, str)
                or not assessment.content_version_binding
            )
        ):
            raise AccessAssessmentError("invalid_access_assessment")
        checked = cls._instant(assessment.checked_at)
        if assessment.expires_at is not None:
            expires = cls._instant(assessment.expires_at)
            if expires <= checked:
                raise AccessAssessmentError("invalid_access_time")
        return uses_json, assessment.evidence_json

    @classmethod
    def _decode(cls, row: sqlite3.Row) -> AccessAssessment:
        try:
            uses = cls._canonical_json(row["permitted_uses_json"], "invalid_access_assessment")
            evidence = cls._canonical_json(row["evidence_json"], "invalid_access_assessment")
            if (
                not isinstance(uses, list)
                or any(not isinstance(item, str) for item in uses)
                or len(uses) != len(set(uses))
                or uses != sorted(uses)
                or not isinstance(evidence, dict)
                or evidence.get("content_scope") not in cls._SCOPES
            ):
                raise AccessAssessmentError("invalid_access_assessment")
            value = AccessAssessment(
                row["id"],
                row["manifestation_id"],
                row["location_url"],
                evidence["content_scope"],
                row["reader_access"],
                row["automated_retrieval"],
                tuple(uses),
                row["license_id"],
                row["evidence_json"],
                row["checked_at"],
                row["expires_at"],
                row["content_version_binding"],
            )
            cls._validate(value)
            return value
        except (KeyError, TypeError, ValueError):
            raise AccessAssessmentError("invalid_access_assessment") from None

    def read_manifestation(self, manifestation_id: str) -> ManifestationAccessIdentity:
        manifestation_id = self._identifier(manifestation_id, "invalid_manifestation_identity")
        with self._transaction(write=False) as connection:
            row = connection.execute(
                "SELECT id,source_namespace,native_id FROM paper_manifestations WHERE id=?",
                (manifestation_id,),
            ).fetchone()
            if row is None:
                raise AccessAssessmentError("manifestation_missing")
            return ManifestationAccessIdentity(row["id"], row["source_namespace"], row["native_id"])

    def save(self, assessment: AccessAssessment) -> AccessAssessment:
        uses_json, evidence_json = self._validate(assessment)
        with self._transaction(write=True) as connection:
            manifestation = connection.execute(
                "SELECT 1 FROM paper_manifestations WHERE id=?",
                (assessment.manifestation_id,),
            ).fetchone()
            if manifestation is None:
                raise AccessAssessmentError("manifestation_missing")
            existing = connection.execute(
                "SELECT * FROM access_assessments WHERE id=?",
                (assessment.assessment_id,),
            ).fetchone()
            if existing is not None:
                restored = self._decode(existing)
                if restored != assessment:
                    raise AccessAssessmentError("access_assessment_conflict")
                return restored
            connection.execute(
                "INSERT INTO access_assessments("
                "id,manifestation_id,location_url,reader_access,automated_retrieval,"
                "permitted_uses_json,license_id,evidence_json,checked_at,expires_at,"
                "content_version_binding"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    assessment.assessment_id,
                    assessment.manifestation_id,
                    assessment.location_url,
                    assessment.reader_access,
                    assessment.automated_retrieval,
                    uses_json,
                    assessment.license_id,
                    evidence_json,
                    assessment.checked_at,
                    assessment.expires_at,
                    assessment.content_version_binding,
                ),
            )
            return assessment

    def read_current(self, manifestation_id: str, at: datetime) -> AccessAssessment:
        manifestation_id = self._identifier(manifestation_id, "invalid_manifestation_identity")
        moment = self._query_time(at)
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                "SELECT * FROM access_assessments WHERE manifestation_id=? "
                "ORDER BY checked_at DESC,id DESC",
                (manifestation_id,),
            ).fetchall()
            if not rows:
                raise AccessAssessmentError("access_assessment_missing")
            saw_effective = False
            for row in rows:
                value = self._decode(row)
                checked = self._instant(value.checked_at)
                if checked > moment:
                    continue
                saw_effective = True
                if value.expires_at is None or self._instant(value.expires_at) > moment:
                    return value
            raise AccessAssessmentError(
                "access_assessment_expired" if saw_effective else "access_assessment_missing"
            )
