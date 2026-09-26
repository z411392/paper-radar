import sqlite3
from collections.abc import Callable

from libs.kernel.dtos.workspace_info import WorkspaceInfo
from libs.kernel.exceptions.storage_error import StorageError


class SqliteWorkspaceExternalEffectsAdapter:
    """Mutate only the workspace master external-effects gate."""

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def set_enabled(
        self,
        enabled: bool,
        *,
        expected_workspace_id: str | None = None,
        expected_epoch: int | None = None,
        expected_enabled: bool | None = None,
    ) -> WorkspaceInfo:
        if type(enabled) is not bool:
            raise StorageError("invalid_external_effects_state")
        expectation_supplied = any(
            value is not None
            for value in (
                expected_workspace_id,
                expected_epoch,
                expected_enabled,
            )
        )
        expected_current: int | None = None
        if expectation_supplied:
            if (
                not isinstance(expected_workspace_id, str)
                or not expected_workspace_id
                or "\0" in expected_workspace_id
                or type(expected_epoch) is not int
                or not 1 <= expected_epoch < 2**63
                or type(expected_enabled) is not bool
            ):
                raise StorageError("invalid_workspace_effects_expectation")
            expected_current = 1 if expected_enabled else 0

        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise StorageError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise StorageError("foreign_keys_required")
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if mode not in {"wal", "delete", "truncate", "persist"}:
                raise StorageError("workspace_effects_durable_journal_required")
            connection.execute("PRAGMA synchronous=EXTRA")
            connection.execute("BEGIN IMMEDIATE")
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
            if expectation_supplied and (
                row["workspace_id"] != expected_workspace_id
                or row["epoch"] != expected_epoch
            ):
                raise StorageError("workspace_effects_conflict")

            requested = 1 if enabled else 0
            current = row["external_effects_enabled"]
            if current != requested:
                if expectation_supplied and current != expected_current:
                    raise StorageError("workspace_effects_conflict")
                changed = connection.execute(
                    "UPDATE workspace_metadata SET external_effects_enabled=? "
                    "WHERE singleton=1 AND workspace_id=? AND epoch=? "
                    "AND external_effects_enabled=?",
                    (
                        requested,
                        row["workspace_id"],
                        row["epoch"],
                        current,
                    ),
                ).rowcount
                if changed != 1:
                    raise StorageError("workspace_effects_conflict")
            final = connection.execute(
                "SELECT workspace_id,epoch,external_effects_enabled "
                "FROM workspace_metadata WHERE singleton=1"
            ).fetchone()
            if (
                final is None
                or final["workspace_id"] != row["workspace_id"]
                or final["epoch"] != row["epoch"]
                or final["external_effects_enabled"] != requested
            ):
                raise StorageError("workspace_effects_conflict")
            connection.commit()
            return WorkspaceInfo(
                final["workspace_id"],
                final["epoch"],
                bool(final["external_effects_enabled"]),
                version,
            )
        except StorageError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise StorageError("workspace_effects_update_failed") from exc
        finally:
            if connection is not None:
                connection.close()
