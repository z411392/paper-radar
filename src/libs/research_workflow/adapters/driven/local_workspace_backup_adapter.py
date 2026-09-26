import hashlib
import json
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

from libs.kernel.adapters.driven.sqlite_connection_factory import (
    APPLICATION_ID,
    SqliteConnectionFactory,
)
from libs.kernel.adapters.driven.workspace_paths import WorkspacePaths
from libs.kernel.dtos.object_ref import ObjectRef
from libs.kernel.exceptions.storage_error import StorageError
from libs.research_workflow.dtos.workspace_backup import WorkspaceBackupResult
from libs.research_workflow.exceptions.workspace_backup_error import WorkspaceBackupError


class LocalWorkspaceBackupAdapter:
    _ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")

    def __init__(self, root: Path) -> None:
        self._paths = WorkspacePaths(root)
        self._factory = SqliteConnectionFactory(root)

    @classmethod
    def _backup_id(cls, value: object) -> str:
        if not isinstance(value, str) or cls._ID.fullmatch(value) is None:
            raise WorkspaceBackupError("invalid_backup_id")
        if value in {".", ".."} or "\x00" in value:
            raise WorkspaceBackupError("invalid_backup_id")
        return value

    @staticmethod
    def _time(value: object) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise WorkspaceBackupError("invalid_backup_created_at")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise WorkspaceBackupError("invalid_backup_created_at") from None

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with os.fdopen(fd, "rb", closefd=False) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        finally:
            os.close(fd)
        return digest.hexdigest()

    @staticmethod
    def _fsync_file(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _canonical_json(payload: object) -> bytes:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    def _atomic_json(self, path: Path, payload: object) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        data = self._canonical_json(payload)
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            WorkspacePaths.sync_directory(path.parent)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)

    def _pin_path(self, run_id: str) -> Path:
        directory = self._paths.directory("state/backup-pins")
        return self._paths.path(f"state/backup-pins/{run_id}.json")

    def _write_pin(
        self,
        run_id: str,
        *,
        object_ids: list[str] | None = None,
    ) -> None:
        payload = {
            "format_version": 1,
            "run_id": run_id,
            "state": "building",
            "scope": (
                "exact_object_ids"
                if object_ids is not None
                else "all_registered_objects"
            ),
            "object_ids": object_ids or [],
        }
        path = self._pin_path(run_id)
        self._atomic_json(path, payload)

    def _remove_pin(self, run_id: str) -> None:
        path = self._pin_path(run_id)
        path.unlink(missing_ok=True)
        WorkspacePaths.sync_directory(path.parent)

    def _create_building_run(
        self,
        run_id: str,
        relative_directory: str,
        created_at: str,
    ) -> str:
        connection = self._factory.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT state FROM backup_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                raise WorkspaceBackupError("backup_id_conflict")
            workspace = connection.execute(
                "SELECT workspace_id FROM workspace_metadata WHERE singleton=1"
            ).fetchone()
            if workspace is None or not workspace["workspace_id"]:
                raise WorkspaceBackupError("backup_workspace_invalid")
            connection.execute(
                "INSERT INTO backup_runs("
                "id,workspace_id,state,relative_directory,created_at"
                ") VALUES(?,?,'building',?,?)",
                (
                    run_id,
                    workspace["workspace_id"],
                    relative_directory,
                    created_at,
                ),
            )
            connection.commit()
            return str(workspace["workspace_id"])
        except WorkspaceBackupError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise WorkspaceBackupError("backup_database_error") from exc
        finally:
            connection.close()

    def _record_failed(self, run_id: str) -> None:
        try:
            connection = self._factory.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE backup_runs SET state='failed' "
                    "WHERE id=? AND state='building'",
                    (run_id,),
                )
                connection.commit()
            finally:
                connection.close()
        except (sqlite3.Error, StorageError):
            return

    def _snapshot_database(self, backup_directory: Path) -> Path:
        backup_paths = WorkspacePaths(backup_directory)
        state_directory = backup_paths.directory("state")
        final = backup_paths.path("state/app.sqlite3")
        staging = backup_paths.path("state/.app.sqlite3.partial")
        if final.exists() or staging.exists():
            raise WorkspaceBackupError("backup_destination_not_empty")

        source: sqlite3.Connection | None = None
        destination: sqlite3.Connection | None = None
        try:
            source = self._factory.connect()
            destination = sqlite3.connect(staging)
            source.backup(destination, pages=256, sleep=0.01)
            destination.close()
            destination = None
            self._fsync_file(staging)
            os.replace(staging, final)
            WorkspacePaths.sync_directory(state_directory)

            check = sqlite3.connect(f"{final.as_uri()}?mode=ro", uri=True)
            try:
                if check.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                    raise WorkspaceBackupError("backup_database_invalid")
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise WorkspaceBackupError("backup_database_invalid")
            finally:
                check.close()
            return final
        except WorkspaceBackupError:
            raise
        except (sqlite3.Error, OSError, StorageError) as exc:
            raise WorkspaceBackupError("backup_snapshot_failed") from exc
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()
            staging.unlink(missing_ok=True)

    def _snapshot_objects(self, database: Path) -> tuple[ObjectRef, ...]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{database.as_uri()}?mode=ro",
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM object_registry ORDER BY object_id"
            ).fetchall()
            refs = tuple(
                ObjectRef(
                    row["object_id"],
                    row["content_sha256"],
                    row["relative_path"],
                    row["kind"],
                    row["media_type"],
                    row["byte_size"],
                    row["created_at"],
                    row["retention_policy"],
                    row["state"],
                )
                for row in rows
            )
            if any(ref.state != "available" for ref in refs):
                raise WorkspaceBackupError("backup_object_unavailable")
            return refs
        except WorkspaceBackupError:
            raise
        except StorageError as exc:
            raise WorkspaceBackupError("backup_registry_corrupt") from exc
        except sqlite3.Error as exc:
            raise WorkspaceBackupError("backup_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

    def _copy_object(
        self,
        object_ref: ObjectRef,
        backup_directory: Path,
    ) -> None:
        backup_paths = WorkspacePaths(backup_directory)
        source_fd: int | None = None
        destination_fd: int | None = None
        staging: Path | None = None
        try:
            source = self._paths.path(object_ref.relative_path)
            source_fd = os.open(
                source,
                os.O_RDONLY | os.O_NOFOLLOW,
            )
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                raise WorkspaceBackupError("backup_object_unsafe")

            relative_parent = Path(object_ref.relative_path).parent.as_posix()
            destination_parent = backup_paths.directory(relative_parent)
            target = backup_paths.path(object_ref.relative_path)
            if target.exists() or target.is_symlink():
                raise WorkspaceBackupError("backup_destination_not_empty")
            staging = target.with_name(f".{target.name}.partial")
            destination_fd = os.open(
                staging,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )

            digest = hashlib.sha256()
            byte_size = 0
            with (
                os.fdopen(source_fd, "rb", closefd=False) as source_stream,
                os.fdopen(destination_fd, "wb", closefd=False) as destination_stream,
            ):
                while chunk := source_stream.read(1024 * 1024):
                    digest.update(chunk)
                    byte_size += len(chunk)
                    destination_stream.write(chunk)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())

            if (
                byte_size != object_ref.byte_size
                or digest.hexdigest() != object_ref.content_sha256
            ):
                raise WorkspaceBackupError("backup_object_corrupt")

            os.close(source_fd)
            source_fd = None
            os.close(destination_fd)
            destination_fd = None
            os.replace(staging, target)
            WorkspacePaths.sync_directory(destination_parent)
            staging = None
        except FileNotFoundError as exc:
            raise WorkspaceBackupError("backup_object_missing") from exc
        except WorkspaceBackupError:
            raise
        except StorageError as exc:
            code = (
                "backup_object_unsafe"
                if exc.code == "unsafe_path"
                else "backup_file_io"
            )
            raise WorkspaceBackupError(code) from exc
        except OSError as exc:
            raise WorkspaceBackupError("backup_file_io") from exc
        finally:
            if source_fd is not None:
                os.close(source_fd)
            if destination_fd is not None:
                os.close(destination_fd)
            if staging is not None:
                staging.unlink(missing_ok=True)

    def _building_manifest(
        self,
        *,
        run_id: str,
        workspace_id: str,
        created_at: str,
        database_sha256: str,
        objects: tuple[ObjectRef, ...] = (),
    ) -> dict[str, object]:
        return {
            "format_version": 1,
            "run_id": run_id,
            "workspace_id": workspace_id,
            "created_at": created_at,
            "state": "building",
            "database": {
                "relative_path": "state/app.sqlite3",
                "sha256": database_sha256,
            },
            "object_count": len(objects),
            "objects": [
                {
                    "object_id": ref.object_id,
                    "content_sha256": ref.content_sha256,
                    "relative_path": ref.relative_path,
                    "kind": ref.kind,
                    "media_type": ref.media_type,
                    "byte_size": ref.byte_size,
                    "retention_policy": ref.retention_policy,
                }
                for ref in objects
            ],
        }

    def backup(
        self,
        run_id: str,
        created_at: datetime,
    ) -> WorkspaceBackupResult:
        run_id = self._backup_id(run_id)
        created = self._time(created_at)
        relative_directory = f"backups/{run_id}"
        inserted = False
        try:
            workspace_id = self._create_building_run(
                run_id,
                relative_directory,
                created,
            )
            inserted = True
            backup_directory = self._paths.directory(relative_directory)
            self._write_pin(run_id)
            database = self._snapshot_database(backup_directory)
            database_sha256 = self._sha256_file(database)
            objects = self._snapshot_objects(database)
            self._write_pin(
                run_id,
                object_ids=[ref.object_id for ref in objects],
            )
            partial = backup_directory / "manifest.partial.json"
            self._atomic_json(
                partial,
                self._building_manifest(
                    run_id=run_id,
                    workspace_id=workspace_id,
                    created_at=created,
                    database_sha256=database_sha256,
                    objects=objects,
                ),
            )
            for object_ref in objects:
                self._copy_object(object_ref, backup_directory)
            return WorkspaceBackupResult(
                run_id=run_id,
                relative_directory=relative_directory,
                database_sha256=database_sha256,
                manifest_sha256="",
                object_count=len(objects),
                state="building",
            )
        except WorkspaceBackupError:
            if inserted:
                self._record_failed(run_id)
                self._remove_pin(run_id)
            raise
        except (OSError, StorageError) as exc:
            if inserted:
                self._record_failed(run_id)
                self._remove_pin(run_id)
            raise WorkspaceBackupError("backup_file_io") from exc
