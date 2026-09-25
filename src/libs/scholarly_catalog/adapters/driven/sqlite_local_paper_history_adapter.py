import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from libs.scholarly_catalog.dtos.local_paper_record import (
    LocalAccessAssessmentView,
    LocalManifestationView,
    LocalPaperRecord,
    LocalPaperRevisionView,
)
from libs.scholarly_catalog.exceptions.local_paper_history_error import (
    LocalPaperHistoryError,
)


class SqliteLocalPaperHistoryAdapter:
    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        maximum_alias_family: int = 10000,
    ) -> None:
        if (
            type(maximum_alias_family) is not int
            or not 1 <= maximum_alias_family <= 100000
        ):
            raise LocalPaperHistoryError("invalid_local_paper_alias_limit")
        self._connect = connect
        self._maximum_alias_family = maximum_alias_family

    @staticmethod
    def _text(value: object, code: str, maximum: int = 512) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\0" in value
        ):
            raise LocalPaperHistoryError(code)
        try:
            if len(value.encode("utf-8")) > maximum:
                raise LocalPaperHistoryError(code)
        except UnicodeEncodeError:
            raise LocalPaperHistoryError(code) from None
        return value

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise LocalPaperHistoryError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise LocalPaperHistoryError("foreign_keys_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except LocalPaperHistoryError:
            raise
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "local_paper_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "local_paper_database_error"
            )
            raise LocalPaperHistoryError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _work(connection: sqlite3.Connection, work_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM paper_works WHERE id=?",
            (work_id,),
        ).fetchone()
        if row is None:
            raise LocalPaperHistoryError("local_paper_missing")
        return row

    def _canonical(
        self,
        connection: sqlite3.Connection,
        requested_work_id: str,
    ) -> str:
        current = requested_work_id
        visited: set[str] = set()
        for _ in range(128):
            self._work(connection, current)
            if current in visited:
                raise LocalPaperHistoryError("local_paper_alias_cycle")
            visited.add(current)
            row = connection.execute(
                "SELECT canonical_work_id FROM work_aliases WHERE alias_work_id=?",
                (current,),
            ).fetchone()
            if row is None:
                return current
            target = row["canonical_work_id"]
            if not isinstance(target, str) or not target:
                raise LocalPaperHistoryError("local_paper_alias_corrupt")
            current = target
        raise LocalPaperHistoryError("local_paper_alias_depth_exceeded")

    def _family(
        self,
        connection: sqlite3.Connection,
        canonical_work_id: str,
    ) -> tuple[str, ...]:
        family = {canonical_work_id}
        frontier = [canonical_work_id]
        while frontier:
            discovered: list[str] = []
            for offset in range(0, len(frontier), 400):
                chunk = frontier[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT alias_work_id,canonical_work_id FROM work_aliases "
                    f"WHERE canonical_work_id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    alias = row["alias_work_id"]
                    target = row["canonical_work_id"]
                    if (
                        not isinstance(alias, str)
                        or not alias
                        or target not in family
                    ):
                        raise LocalPaperHistoryError("local_paper_alias_corrupt")
                    self._work(connection, alias)
                    if alias in family:
                        raise LocalPaperHistoryError("local_paper_alias_cycle")
                    family.add(alias)
                    discovered.append(alias)
                    if len(family) > self._maximum_alias_family:
                        raise LocalPaperHistoryError(
                            "local_paper_alias_limit_exceeded"
                        )
            frontier = discovered
        return tuple(sorted(family))

    @staticmethod
    def _json_list(value: object) -> tuple[str, ...]:
        if not isinstance(value, str):
            raise LocalPaperHistoryError("local_paper_access_corrupt")
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            raise LocalPaperHistoryError("local_paper_access_corrupt") from None
        if (
            not isinstance(decoded, list)
            or any(not isinstance(item, str) or not item for item in decoded)
            or len(decoded) != len(set(decoded))
        ):
            raise LocalPaperHistoryError("local_paper_access_corrupt")
        return tuple(decoded)

    @staticmethod
    def _content_scope(value: object) -> str:
        if not isinstance(value, str):
            raise LocalPaperHistoryError("local_paper_access_corrupt")
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            raise LocalPaperHistoryError("local_paper_access_corrupt") from None
        scope = decoded.get("content_scope") if isinstance(decoded, dict) else None
        if scope not in {"abstract", "full_text"}:
            raise LocalPaperHistoryError("local_paper_access_corrupt")
        return scope

    def read(self, work_id: str) -> LocalPaperRecord:
        requested = self._text(
            work_id,
            "invalid_local_paper_work_id",
            256,
        )
        with self._transaction() as connection:
            canonical = self._canonical(connection, requested)
            canonical_row = self._work(connection, canonical)
            family = self._family(connection, canonical)

            manifestations: list[sqlite3.Row] = []
            for offset in range(0, len(family), 400):
                chunk = family[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                manifestations.extend(
                    connection.execute(
                        "SELECT * FROM paper_manifestations "
                        f"WHERE work_id IN ({placeholders})",
                        tuple(chunk),
                    ).fetchall()
                )
            manifestations.sort(
                key=lambda row: (
                    row["source_namespace"],
                    row["native_id"],
                    row["id"],
                )
            )

            revision_rows: dict[str, list[sqlite3.Row]] = {}
            access_rows: dict[str, list[sqlite3.Row]] = {}
            manifestation_ids = [row["id"] for row in manifestations]
            for offset in range(0, len(manifestation_ids), 400):
                chunk = manifestation_ids[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                for row in connection.execute(
                    "SELECT r.*,o.state AS abstract_object_state "
                    "FROM paper_revisions r "
                    "LEFT JOIN object_registry o ON o.object_id=r.abstract_object_id "
                    f"WHERE r.manifestation_id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall():
                    if (
                        row["abstract_object_id"] is not None
                        and row["abstract_object_state"] != "available"
                    ):
                        raise LocalPaperHistoryError(
                            "local_paper_object_unavailable"
                        )
                    revision_rows.setdefault(row["manifestation_id"], []).append(row)
                for row in connection.execute(
                    "SELECT * FROM access_assessments "
                    f"WHERE manifestation_id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall():
                    access_rows.setdefault(row["manifestation_id"], []).append(row)

            values = []
            for manifestation in manifestations:
                revisions = sorted(
                    revision_rows.get(manifestation["id"], []),
                    key=lambda row: (row["observed_at"], row["id"]),
                )
                assessments = sorted(
                    access_rows.get(manifestation["id"], []),
                    key=lambda row: (row["checked_at"], row["id"]),
                )
                values.append(
                    LocalManifestationView(
                        manifestation["id"],
                        manifestation["work_id"],
                        manifestation["source_namespace"],
                        manifestation["native_id"],
                        manifestation["manifestation_kind"],
                        manifestation["landing_url"],
                        tuple(
                            LocalPaperRevisionView(
                                row["id"],
                                row["manifestation_id"],
                                row["work_id"],
                                row["native_version"],
                                row["content_fingerprint"],
                                row["title"],
                                row["source_updated_at"],
                                row["published_date"],
                                row["date_precision"],
                                row["observed_at"],
                            )
                            for row in revisions
                        ),
                        tuple(
                            LocalAccessAssessmentView(
                                row["id"],
                                row["manifestation_id"],
                                row["location_url"],
                                self._content_scope(row["evidence_json"]),
                                row["reader_access"],
                                row["automated_retrieval"],
                                self._json_list(row["permitted_uses_json"]),
                                row["license_id"],
                                row["checked_at"],
                                row["expires_at"],
                                row["content_version_binding"],
                            )
                            for row in assessments
                        ),
                    )
                )

            return LocalPaperRecord(
                requested,
                canonical,
                canonical_row["canonical_title"],
                canonical_row["publication_status"],
                canonical_row["first_public_date"],
                canonical_row["first_public_precision"],
                family,
                tuple(values),
            )
