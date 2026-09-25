import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from libs.scholarly_catalog.dtos.digest_research_event import DigestResearchEvent
from libs.scholarly_catalog.exceptions.digest_event_read_error import DigestEventReadError


class SqliteDigestResearchEventAdapter:
    _PAPER_KINDS = (
        "new_work",
        "late_discovery",
        "revision_available",
        "newly_accessible",
    )
    _STATUS_KINDS = ("correction", "retraction")
    _KINDS = _PAPER_KINDS + _STATUS_KINDS

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _instant(value: object, code: str) -> datetime:
        if isinstance(value, datetime):
            moment = value
        elif isinstance(value, str):
            try:
                moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, OverflowError):
                raise DigestEventReadError(code) from None
        else:
            raise DigestEventReadError(code)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise DigestEventReadError(code)
        return moment.astimezone(timezone.utc)

    def __call__(
        self,
        period_start: datetime,
        cutoff_at: datetime,
    ) -> tuple[DigestResearchEvent, ...]:
        start = self._instant(period_start, "invalid_digest_period")
        cutoff = self._instant(cutoff_at, "invalid_digest_period")
        if start >= cutoff:
            raise DigestEventReadError("invalid_digest_period")
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise DigestEventReadError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            placeholders = ",".join("?" for _ in self._KINDS)
            rows = connection.execute(
                "SELECT e.id AS event_id,e.work_id,e.revision_id,e.event_kind,"
                "e.observed_at,COALESCE(r.title,w.canonical_title) AS title,"
                "COALESCE(m.landing_url,("
                "SELECT pm.landing_url FROM paper_manifestations pm "
                "WHERE pm.work_id=e.work_id ORDER BY pm.created_at,pm.id LIMIT 1"
                ")) AS landing_url "
                "FROM research_events e "
                "JOIN paper_works w ON w.id=e.work_id "
                "LEFT JOIN paper_revisions r "
                "ON r.id=e.revision_id AND r.work_id=e.work_id "
                "LEFT JOIN paper_manifestations m ON m.id=r.manifestation_id "
                "WHERE julianday(e.observed_at)>julianday(?) "
                "AND julianday(e.observed_at)<=julianday(?) "
                f"AND e.event_kind IN ({placeholders}) "
                "ORDER BY e.observed_at,e.id",
                (start.isoformat(), cutoff.isoformat(), *self._KINDS),
            ).fetchall()
            connection.commit()
            result = []
            for row in rows:
                common = ("event_id", "work_id", "event_kind", "title")
                if not all(
                    isinstance(row[key], str) and row[key]
                    for key in common
                ):
                    raise DigestEventReadError("digest_event_corrupt")
                kind = row["event_kind"]
                revision_id = row["revision_id"]
                source_url = row["landing_url"]
                if kind in self._PAPER_KINDS:
                    if (
                        not isinstance(revision_id, str)
                        or not revision_id
                        or not isinstance(source_url, str)
                        or not source_url
                    ):
                        raise DigestEventReadError("digest_event_corrupt")
                elif kind in self._STATUS_KINDS:
                    if revision_id is not None and (
                        not isinstance(revision_id, str) or not revision_id
                    ):
                        raise DigestEventReadError("digest_event_corrupt")
                    if source_url is not None and (
                        not isinstance(source_url, str) or not source_url
                    ):
                        raise DigestEventReadError("digest_event_corrupt")
                else:
                    raise DigestEventReadError("digest_event_corrupt")
                result.append(
                    DigestResearchEvent(
                        row["event_id"],
                        row["work_id"],
                        revision_id,
                        kind,
                        self._instant(row["observed_at"], "digest_event_corrupt"),
                        row["title"],
                        source_url,
                    )
                )
            return tuple(result)
        except DigestEventReadError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise DigestEventReadError("digest_event_database_error") from exc
        finally:
            if connection is not None:
                connection.close()
