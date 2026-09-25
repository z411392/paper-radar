import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class SqliteResearchEventStoreAdapter:
    _KINDS = frozenset(
        {
            "new_work",
            "late_discovery",
            "revision_available",
            "publication_status_changed",
            "correction",
            "retraction",
            "newly_accessible",
            "metadata_changed",
        }
    )

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _text(value: object, maximum: int) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise PaperIdentityError("invalid_research_event")
        try:
            if len(value.encode("utf-8")) > maximum:
                raise PaperIdentityError("invalid_research_event")
        except UnicodeEncodeError:
            raise PaperIdentityError("invalid_research_event") from None
        return value

    @staticmethod
    def _time(value: object, *, nullable: bool = False) -> str | None:
        if value is None and nullable:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise PaperIdentityError("invalid_research_event")
        try:
            if value.utcoffset() is None:
                raise ValueError
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise PaperIdentityError("invalid_research_event") from None

    @staticmethod
    def _evidence(value: object) -> str:
        if not isinstance(value, str):
            raise PaperIdentityError("invalid_research_event")
        try:
            parsed = json.loads(value)
            canonical = json.dumps(
                parsed,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            raise PaperIdentityError("invalid_research_event") from None
        if canonical != value:
            raise PaperIdentityError("invalid_research_event")
        return canonical

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise PaperIdentityError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise PaperIdentityError("foreign_keys_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except PaperIdentityError:
            raise
        except sqlite3.IntegrityError as exc:
            raise PaperIdentityError("research_event_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "research_event_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "research_event_database_error"
            )
            raise PaperIdentityError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    def register(
        self,
        *,
        event_id: str,
        work_id: str,
        revision_id: str | None,
        event_kind: str,
        canonical_event_key: str,
        source_evidence_json: str,
        occurred_at: datetime | None,
        observed_at: datetime,
    ) -> str:
        event_id = self._text(event_id, 256)
        work_id = self._text(work_id, 256)
        if revision_id is not None:
            revision_id = self._text(revision_id, 256)
        if event_kind not in self._KINDS:
            raise PaperIdentityError("invalid_research_event")
        canonical_event_key = self._text(canonical_event_key, 512)
        evidence = self._evidence(source_evidence_json)
        occurred = self._time(occurred_at, nullable=True)
        observed = self._time(observed_at)
        assert observed is not None

        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM paper_works WHERE id=?",
                (work_id,),
            ).fetchone() is None:
                raise PaperIdentityError("work_missing")
            if revision_id is not None:
                revision = connection.execute(
                    "SELECT work_id FROM paper_revisions WHERE id=?",
                    (revision_id,),
                ).fetchone()
                if revision is None or revision["work_id"] != work_id:
                    raise PaperIdentityError("revision_conflict")

            existing = connection.execute(
                "SELECT * FROM research_events WHERE canonical_event_key=?",
                (canonical_event_key,),
            ).fetchone()
            if existing is not None:
                expected = (
                    event_id,
                    work_id,
                    revision_id,
                    event_kind,
                    canonical_event_key,
                    evidence,
                    occurred,
                )
                actual = (
                    existing["id"],
                    existing["work_id"],
                    existing["revision_id"],
                    existing["event_kind"],
                    existing["canonical_event_key"],
                    existing["source_evidence_json"],
                    existing["occurred_at"],
                )
                if actual != expected:
                    raise PaperIdentityError("research_event_conflict")
                return existing["id"]

            connection.execute(
                "INSERT INTO research_events("
                "id,work_id,revision_id,event_kind,canonical_event_key,"
                "source_evidence_json,occurred_at,observed_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    work_id,
                    revision_id,
                    event_kind,
                    canonical_event_key,
                    evidence,
                    occurred,
                    observed,
                ),
            )
            return event_id
