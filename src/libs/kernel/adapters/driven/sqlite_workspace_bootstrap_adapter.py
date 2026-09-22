import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from libs.kernel.adapters.driven.sqlite_connection_factory import APPLICATION_ID, SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_migration_runner import SqliteMigrationRunner
from libs.kernel.adapters.driven.workspace_paths import WorkspacePaths
from libs.kernel.dtos.migration import Migration
from libs.kernel.dtos.workspace_info import WorkspaceInfo
from libs.kernel.exceptions.storage_error import StorageError


class SqliteWorkspaceBootstrapAdapter:
    def __init__(self, root: Path, migrations: tuple[Migration, ...]) -> None:
        self._paths = WorkspacePaths(root)
        self._runner = SqliteMigrationRunner(migrations)

    def initialize(self) -> WorkspaceInfo:
        try:
            root = self._paths.root
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            for relative in ("state", "objects", "tmp"):
                self._paths.path(relative)
            database = self._paths.database()
            if not database.exists():
                allowed = {"state", "objects", "tmp"}
                if (
                    any(p.name not in allowed for p in root.iterdir())
                    and not self._paths.database().is_file()
                ):
                    raise StorageError("foreign_workspace", "not an empty or interrupted bootstrap directory")
                for relative in ("objects", "tmp", "state"):
                    directory = self._paths.path(relative)
                    if not directory.exists():
                        continue
                    if not directory.is_dir():
                        raise StorageError("unsafe_path", relative)
                    for child in directory.iterdir():
                        if relative == "state" and child.name.startswith(".bootstrap-") and child.is_file():
                            if child.is_symlink():
                                raise StorageError("unsafe_path", child.name)
                            continue
                        # Another initializer may have published a complete database
                        # since the initial existence check. Validate it below.
                        if not self._paths.database().is_file():
                            raise StorageError(
                                "foreign_workspace", "existing data without a recognized database"
                            )
                state = self._paths.directory("state")
                self._paths.directory("objects")
                self._paths.directory("tmp")
                self._create_database(state, database)
            connection = SqliteConnectionFactory(root).connect()
            try:
                version = self._runner.apply(connection)
                rows = connection.execute(
                    "SELECT workspace_id,epoch,external_effects_enabled "
                    "FROM workspace_metadata WHERE singleton=1"
                ).fetchall()
                if len(rows) != 1:
                    raise StorageError("invalid_workspace", "missing singleton identity")
                row = rows[0]
                return WorkspaceInfo(row[0], row[1], bool(row[2]), version)
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise StorageError("workspace_database_error", str(exc)) from exc
        except OSError as exc:
            raise StorageError("file_io", str(exc)) from exc

    def _create_database(self, state: Path, destination: Path) -> None:
        fd, name = tempfile.mkstemp(prefix=".bootstrap-", suffix=".sqlite3", dir=state)
        os.close(fd)
        staging = Path(name)
        try:
            connection = sqlite3.connect(staging, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA synchronous=FULL")
                self._runner.apply(connection)
                connection.execute("BEGIN IMMEDIATE")
                with connection:
                    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    connection.execute(
                        "INSERT INTO workspace_metadata(singleton,workspace_id,epoch,"
                        "external_effects_enabled,"
                        "created_at) VALUES(1,?,1,0,?)",
                        (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat()),
                    )
            finally:
                connection.close()
            fd = os.open(staging, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(staging, destination)
            except FileExistsError:
                pass
            self._paths.sync_directory(state)
        finally:
            staging.unlink(missing_ok=True)
