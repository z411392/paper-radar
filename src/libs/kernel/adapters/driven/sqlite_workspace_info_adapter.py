import sqlite3
from collections.abc import Callable

from libs.kernel.dtos.workspace_info import WorkspaceInfo
from libs.kernel.exceptions.storage_error import StorageError


class SqliteWorkspaceInfoAdapter:
    """Read current workspace authority without mutating or upgrading the workspace."""

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def __call__(self) -> WorkspaceInfo:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise StorageError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT workspace_id,epoch,external_effects_enabled "
                "FROM workspace_metadata WHERE singleton=1"
            ).fetchone()
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            if (
                row is None
                or not isinstance(row["workspace_id"], str)
                or not row["workspace_id"]
                or type(row["epoch"]) is not int
                or not 1 <= row["epoch"] < 2**63
                or row["external_effects_enabled"] not in {0, 1}
                or type(version) is not int
                or version < 1
            ):
                raise StorageError("workspace_metadata_invalid")
            connection.commit()
            return WorkspaceInfo(
                row["workspace_id"],
                row["epoch"],
                bool(row["external_effects_enabled"]),
                version,
            )
        except StorageError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise StorageError("workspace_metadata_read_failed") from exc
        finally:
            if connection is not None:
                connection.close()
