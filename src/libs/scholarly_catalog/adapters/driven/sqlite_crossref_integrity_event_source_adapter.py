import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.scholarly_catalog.dtos.crossref_integrity_event_source import (
    CrossrefIntegrityEventSource,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


class SqliteCrossrefIntegrityEventSourceAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _text(value: object, maximum: int) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise CrossrefProviderProjectionError(
                "crossref_integrity_event_source_corrupt"
            )
        try:
            if len(value.encode("utf-8")) > maximum:
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_event_source_corrupt"
                )
        except UnicodeEncodeError:
            raise CrossrefProviderProjectionError(
                "crossref_integrity_event_source_corrupt"
            ) from None
        return value

    @staticmethod
    def _time(value: object) -> datetime:
        if not isinstance(value, str):
            raise CrossrefProviderProjectionError(
                "crossref_integrity_event_source_corrupt"
            )
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise CrossrefProviderProjectionError(
                "crossref_integrity_event_source_corrupt"
            ) from None
        if result.tzinfo is None or result.utcoffset() is None:
            raise CrossrefProviderProjectionError(
                "crossref_integrity_event_source_corrupt"
            )
        return result.astimezone(timezone.utc)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise CrossrefProviderProjectionError(
                    "owned_connection_required"
                )
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise CrossrefProviderProjectionError(
                    "foreign_keys_required"
                )
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except CrossrefProviderProjectionError:
            raise
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "crossref_integrity_event_source_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "crossref_integrity_event_source_database_error"
            )
            raise CrossrefProviderProjectionError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    def __call__(
        self,
        assertion_id: str,
    ) -> CrossrefIntegrityEventSource | None:
        assertion_id = self._text(assertion_id, 256)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT a.*,b.canonical_doi AS bound_doi,"
                "b.manifestation_id,b.work_id,b.canonical_work_id,"
                "p.work_id AS manifestation_work_id,e.manifestation_id AS doi_manifestation,"
                "cw.id AS canonical_work_exists "
                "FROM crossref_integrity_assertions a "
                "LEFT JOIN crossref_integrity_work_bindings b "
                "ON b.assertion_id=a.id AND b.role='target' "
                "LEFT JOIN paper_manifestations p ON p.id=b.manifestation_id "
                "LEFT JOIN external_identifiers e "
                "ON e.namespace='doi' AND e.normalized_value=b.canonical_doi "
                "AND e.manifestation_id=b.manifestation_id "
                "LEFT JOIN paper_works cw ON cw.id=b.canonical_work_id "
                "WHERE a.id=?",
                (assertion_id,),
            ).fetchone()
            if row is None:
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_event_assertion_missing"
                )
            if row["canonical_work_id"] is None:
                return None
            if (
                row["target_canonical_doi"] is None
                or row["bound_doi"] != row["target_canonical_doi"]
                or row["manifestation_id"] is None
                or row["work_id"] is None
                or row["manifestation_work_id"] != row["work_id"]
                or row["doi_manifestation"] != row["manifestation_id"]
                or row["canonical_work_exists"] != row["canonical_work_id"]
            ):
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_event_binding_corrupt"
                )
            return CrossrefIntegrityEventSource(
                row["id"],
                row["event_class"],
                row["wire_direction"],
                row["notice_canonical_doi"],
                row["target_canonical_doi"],
                row["type_raw"],
                row["source_raw"],
                row["label_raw"],
                row["record_id_raw_json"],
                row["updated_value"],
                row["updated_precision"],
                row["updated_raw_json"],
                row["raw_json"],
                self._time(row["first_observed_at"]),
                row["canonical_work_id"],
            )
