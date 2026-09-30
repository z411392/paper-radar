import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityWorkBindingDraft,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


class SqliteCrossrefIntegrityWorkBindingStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _text(value: object, maximum: int) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_binding"
            )
        try:
            if len(value.encode("utf-8")) > maximum:
                raise CrossrefProviderProjectionError(
                    "invalid_crossref_integrity_binding"
                )
        except UnicodeEncodeError:
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_binding"
            ) from None
        return value

    @staticmethod
    def _time(value: object) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_binding"
            )
        try:
            if value.utcoffset() is None:
                raise ValueError
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_binding"
            ) from None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise CrossrefProviderProjectionError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise CrossrefProviderProjectionError("foreign_keys_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except CrossrefProviderProjectionError:
            raise
        except sqlite3.IntegrityError as exc:
            raise CrossrefProviderProjectionError(
                "crossref_integrity_binding_database_conflict"
            ) from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "crossref_integrity_binding_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "crossref_integrity_binding_database_error"
            )
            raise CrossrefProviderProjectionError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _canonical_work(
        connection: sqlite3.Connection,
        work_id: str,
    ) -> str:
        current = work_id
        visited: set[str] = set()
        for _ in range(128):
            if current in visited:
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_binding_identity_corrupt"
                )
            visited.add(current)
            if connection.execute(
                "SELECT 1 FROM paper_works WHERE id=?",
                (current,),
            ).fetchone() is None:
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_binding_identity_corrupt"
                )
            row = connection.execute(
                "SELECT canonical_work_id FROM work_aliases WHERE alias_work_id=?",
                (current,),
            ).fetchone()
            if row is None:
                return current
            current = row["canonical_work_id"]
        raise CrossrefProviderProjectionError(
            "crossref_integrity_binding_identity_corrupt"
        )

    def register(self, draft: CrossrefIntegrityWorkBindingDraft) -> None:
        if not isinstance(draft, CrossrefIntegrityWorkBindingDraft):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_binding"
            )
        assertion_id = self._text(draft.assertion_id, 256)
        if draft.role not in {"notice", "target"}:
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_binding"
            )
        doi = self._text(draft.canonical_doi, 512)
        manifestation = self._text(draft.manifestation_id, 256)
        work = self._text(draft.work_id, 256)
        canonical_work = self._text(draft.canonical_work_id, 256)
        bound = self._time(draft.bound_at)
        with self._transaction() as connection:
            assertion = connection.execute(
                "SELECT notice_canonical_doi,target_canonical_doi "
                "FROM crossref_integrity_assertions WHERE id=?",
                (assertion_id,),
            ).fetchone()
            if assertion is None or assertion[draft.role + "_canonical_doi"] != doi:
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_binding_assertion_mismatch"
                )
            identity = connection.execute(
                "SELECT p.id,p.work_id FROM external_identifiers e "
                "JOIN paper_manifestations p ON p.id=e.manifestation_id "
                "WHERE e.namespace='doi' AND e.normalized_value=?",
                (doi,),
            ).fetchone()
            if (
                identity is None
                or identity["id"] != manifestation
                or identity["work_id"] != work
            ):
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_binding_identity_mismatch"
                )
            if self._canonical_work(connection, work) != canonical_work:
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_binding_identity_mismatch"
                )
            existing = connection.execute(
                "SELECT * FROM crossref_integrity_work_bindings "
                "WHERE assertion_id=? AND role=?",
                (assertion_id, draft.role),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO crossref_integrity_work_bindings "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        assertion_id,
                        draft.role,
                        doi,
                        manifestation,
                        work,
                        canonical_work,
                        bound,
                    ),
                )
                return
            if (
                existing["canonical_doi"] != doi
                or existing["manifestation_id"] != manifestation
                or existing["work_id"] != work
            ):
                raise CrossrefProviderProjectionError(
                    "crossref_integrity_binding_conflict"
                )
            # canonical_work_id is a historical snapshot and may later change by aliasing.
