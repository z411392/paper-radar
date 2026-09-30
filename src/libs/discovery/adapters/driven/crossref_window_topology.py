import sqlite3
from collections.abc import Sequence

from libs.discovery.exceptions.crossref_repair_error import CrossrefRepairError


class CrossrefWindowTopology:
    """Compatibility with v12 readers; v13 split writes require the installed table."""

    @staticmethod
    def _installed(connection: sqlite3.Connection) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='crossref_window_splits'"
        ).fetchone() is not None

    @classmethod
    def require_leaf(cls, connection: sqlite3.Connection, window_id: str) -> None:
        if cls._installed(connection) and connection.execute(
            "SELECT 1 FROM crossref_window_splits WHERE parent_window_id=?", (window_id,),
        ).fetchone():
            raise CrossrefRepairError("crossref_window_superseded")

    @classmethod
    def leaves(cls, connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> list[sqlite3.Row]:
        if not cls._installed(connection):
            return list(rows)
        return [row for row in rows if not connection.execute(
            "SELECT 1 FROM crossref_window_splits WHERE parent_window_id=?", (row["id"],),
        ).fetchone()]
