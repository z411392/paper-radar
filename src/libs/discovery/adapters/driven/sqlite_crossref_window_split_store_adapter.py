import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.domain.services.crossref_window_split_rules import CrossrefWindowSplitRules as Rules
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.crossref_window_split import CrossrefWindowSplit
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.exceptions.crossref_window_split_error import CrossrefWindowSplitError
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort


class SqliteCrossrefWindowSplitStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection],
                 *, source: CrossrefPageSourcePort | None = None) -> None:
        self._connect = connect
        self._source = source if source is not None else CrossrefSourceAdapter()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise CrossrefWindowSplitError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise CrossrefWindowSplitError("foreign_keys_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except CrossrefWindowSplitError:
            raise
        except sqlite3.IntegrityError as exc:
            raise CrossrefWindowSplitError("crossref_split_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = ("crossref_split_busy" if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                    else "crossref_split_database_error")
            raise CrossrefWindowSplitError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _existing(
        connection: sqlite3.Connection, plan: CrossrefWindowPlan, *, child: bool,
    ) -> sqlite3.Row | None:
        row = connection.execute("SELECT * FROM crossref_harvest_windows WHERE id=?",
                                 (Rules.window_id(plan),)).fetchone()
        if row is not None:
            actual = tuple(row[key] for key in
                           ("binding_key", "query_fingerprint", "config_version",
                            "from_index", "until_index", "rows"))
            if actual != Rules.values(plan):
                code = "crossref_split_child_conflict" if child else "crossref_split_parent_conflict"
                raise CrossrefWindowSplitError(code)
        return row

    @staticmethod
    def _guard_parent(connection: sqlite3.Connection, row: sqlite3.Row, created: datetime) -> None:
        if row["state"] == "running" or connection.execute(
            "SELECT 1 FROM crossref_harvest_passes WHERE window_id=? AND state='running' LIMIT 1",
            (row["id"],),
        ).fetchone() or connection.execute(
            "SELECT 1 FROM crossref_repair_runs WHERE window_id=? AND state='running' LIMIT 1",
            (row["id"],),
        ).fetchone():
            raise CrossrefWindowSplitError("crossref_split_parent_running")
        if connection.execute("SELECT 1 FROM crossref_window_finalizations WHERE window_id=? LIMIT 1",
                              (row["id"],)).fetchone():
            raise CrossrefWindowSplitError("crossref_split_parent_finalized")
        try:
            if Rules.instant(datetime.fromisoformat(row["created_at"])) > created:
                raise CrossrefWindowSplitError("invalid_crossref_split_time")
        except (ValueError, TypeError):
            raise CrossrefWindowSplitError("crossref_split_parent_conflict") from None
        # Follow only the ancestor chain; disallow ambiguous ownership and excessive depth.
        key, seen = row["id"], set()
        for _ in range(Rules.MAX_DEPTH):
            if key in seen:
                raise CrossrefWindowSplitError("crossref_split_topology_corrupt")
            seen.add(key)
            owners = connection.execute(
                "SELECT parent_window_id FROM crossref_window_splits "
                "WHERE left_window_id=? OR right_window_id=?",
                (key, key),
            ).fetchall()
            if not owners:
                return
            if len(owners) != 1:
                raise CrossrefWindowSplitError("crossref_split_topology_corrupt")
            key = owners[0][0]
        raise CrossrefWindowSplitError("crossref_split_depth_limit")

    @classmethod
    def _child(cls, connection: sqlite3.Connection, plan: CrossrefWindowPlan, created: str) -> None:
        row = cls._existing(connection, plan, child=True)
        key = Rules.window_id(plan)
        if row is not None:
            # An independently active/finalized child is not silently reparented.
            if row["state"] != "pending" or connection.execute(
                "SELECT 1 FROM crossref_harvest_passes WHERE window_id=? LIMIT 1", (key,),
            ).fetchone():
                raise CrossrefWindowSplitError("crossref_split_child_in_use")
            return
        connection.execute(
            "INSERT INTO crossref_harvest_windows("
            "id,binding_key,query_fingerprint,config_version,from_index,until_index,"
            "rows,state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'pending',?,?)", (key, *Rules.values(plan), created, created),
        )

    def split(self, parent: CrossrefWindowPlan, left: CrossrefWindowPlan, right: CrossrefWindowPlan,
              *, reason: str, created_at: datetime) -> CrossrefWindowSplit:
        created = Rules.instant(created_at)
        reason = Rules.reason(reason)
        try:
            for value in (parent, left, right):
                if (not isinstance(value, CrossrefWindowPlan)
                        or self._source.compile(value.definition) != value):
                    raise CrossrefWindowSplitError("invalid_crossref_split_plan")
            boundary = Rules.boundary(parent, left.definition.until_index)
            if (left.definition != replace(parent.definition, until_index=boundary)
                    or right.definition != replace(parent.definition, from_index=boundary)):
                raise CrossrefWindowSplitError("invalid_crossref_split_children")
        except CrossrefProtocolError as exc:
            raise CrossrefWindowSplitError("invalid_crossref_split_plan") from exc
        parent_id, left_id, right_id = (Rules.window_id(value) for value in (parent, left, right))
        with self._transaction() as connection:
            row = self._existing(connection, parent, child=False)
            if row is None:
                raise CrossrefWindowSplitError("crossref_split_parent_missing")
            existing = connection.execute("SELECT * FROM crossref_window_splits WHERE parent_window_id=?",
                                          (parent_id,)).fetchone()
            replayed = existing is not None
            if existing is not None:
                if (existing["left_window_id"], existing["right_window_id"], existing["split_at"],
                    existing["reason"]) != (left_id, right_id, boundary.isoformat(), reason):
                    raise CrossrefWindowSplitError("crossref_split_conflict")
                if self._existing(connection, left, child=True) is None or self._existing(
                    connection, right, child=True
                ) is None:
                    raise CrossrefWindowSplitError("crossref_split_topology_corrupt")
                created = Rules.instant(datetime.fromisoformat(existing["created_at"]))
            else:
                self._guard_parent(connection, row, created)
                self._child(connection, left, created.isoformat())
                self._child(connection, right, created.isoformat())
                connection.execute(
                    "INSERT INTO crossref_window_splits("
                    "parent_window_id,left_window_id,right_window_id,split_at,reason,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (parent_id, left_id, right_id, boundary.isoformat(), reason, created.isoformat()),
                )
            return CrossrefWindowSplit(parent_id, left_id, right_id, parent.definition.from_index, boundary,
                                       boundary, parent.definition.until_index, reason, created, replayed)
