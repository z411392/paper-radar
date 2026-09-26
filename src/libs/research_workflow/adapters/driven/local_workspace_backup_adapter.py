import hashlib
import json
import os
import re
import shutil
import tempfile
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import (
    FilesystemObjectBytesAdapter,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import (
    APPLICATION_ID,
    SqliteConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.workspace_paths import WorkspacePaths
from libs.kernel.application.queries.read_object import ReadObject
from libs.kernel.dtos.migration import Migration
from libs.kernel.dtos.object_ref import ObjectRef
from libs.kernel.exceptions.storage_error import StorageError
from libs.research_workflow.dtos.workspace_backup import WorkspaceBackupResult
from libs.research_workflow.dtos.workspace_restore import WorkspaceRestoreResult
from libs.research_workflow.exceptions.workspace_backup_error import WorkspaceBackupError
from libs.research_workflow.exceptions.workspace_restore_error import WorkspaceRestoreError


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

    def _best_effort_remove_pin(self, run_id: str) -> None:
        try:
            self._remove_pin(run_id)
        except (OSError, StorageError):
            return

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

    def _mark_verified(
        self,
        run_id: str,
        *,
        database_sha256: str,
        manifest_sha256: str,
        object_count: int,
    ) -> None:
        verified_at = datetime.now(timezone.utc).isoformat()
        connection = self._factory.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE backup_runs SET "
                "state='verified',database_sha256=?,manifest_sha256=?,"
                "object_count=?,verified_at=? "
                "WHERE id=? AND state='building'",
                (
                    database_sha256,
                    manifest_sha256,
                    object_count,
                    verified_at,
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise WorkspaceBackupError("backup_receipt_conflict")
            connection.commit()
        except WorkspaceBackupError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise WorkspaceBackupError("backup_database_error") from exc
        finally:
            connection.close()

    @staticmethod
    def _verified_manifest(
        building: dict[str, object],
    ) -> dict[str, object]:
        manifest = dict(building)
        manifest["state"] = "verified"
        manifest["integrity"] = {
            "database": "ok",
            "objects": "ok",
            "hash_algorithm": "sha256",
        }
        return manifest

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
            building_manifest = self._building_manifest(
                run_id=run_id,
                workspace_id=workspace_id,
                created_at=created,
                database_sha256=database_sha256,
                objects=objects,
            )
            self._atomic_json(partial, building_manifest)
            for object_ref in objects:
                self._copy_object(object_ref, backup_directory)

            manifest = backup_directory / "manifest.json"
            self._atomic_json(
                manifest,
                self._verified_manifest(building_manifest),
            )
            manifest_sha256 = self._sha256_file(manifest)
            partial.unlink(missing_ok=True)
            WorkspacePaths.sync_directory(backup_directory)
            self._remove_pin(run_id)
            self._mark_verified(
                run_id,
                database_sha256=database_sha256,
                manifest_sha256=manifest_sha256,
                object_count=len(objects),
            )
            return WorkspaceBackupResult(
                run_id=run_id,
                relative_directory=relative_directory,
                database_sha256=database_sha256,
                manifest_sha256=manifest_sha256,
                object_count=len(objects),
                state="verified",
            )
        except WorkspaceBackupError:
            if inserted:
                self._record_failed(run_id)
                self._best_effort_remove_pin(run_id)
            raise
        except (OSError, StorageError) as exc:
            if inserted:
                self._record_failed(run_id)
                self._best_effort_remove_pin(run_id)
            raise WorkspaceBackupError("backup_file_io") from exc

class LocalWorkspaceRestoreAdapter:
    _MANIFEST_LIMIT = 4 * 1024 * 1024
    _HEX = re.compile(r"[0-9a-f]{64}\\Z")

    def __init__(self, migrations: tuple[Migration, ...]) -> None:
        if (
            not migrations
            or [migration.version for migration in migrations]
            != list(range(1, len(migrations) + 1))
        ):
            raise WorkspaceRestoreError("restore_migrations_invalid")
        self._migrations = migrations
        self._expected_migrations = tuple(
            (migration.version, migration.name, migration.sha256)
            for migration in migrations
        )

    @staticmethod
    def _input_path(value: object, code: str) -> Path:
        if not isinstance(value, Path) or ".." in value.parts:
            raise WorkspaceRestoreError(code)
        return value

    def _validate_paths(
        self,
        backup_directory: Path,
        target: Path,
    ) -> tuple[Path, Path]:
        backup_directory = self._input_path(
            backup_directory,
            "restore_source_invalid",
        )
        target = self._input_path(target, "restore_target_unsafe")
        if (
            backup_directory.is_symlink()
            or not backup_directory.is_dir()
        ):
            raise WorkspaceRestoreError("restore_source_invalid")
        if target.is_symlink():
            raise WorkspaceRestoreError("restore_target_unsafe")
        if not target.parent.is_dir() or target.parent.is_symlink():
            raise WorkspaceRestoreError("restore_target_unsafe")

        source_resolved = backup_directory.resolve()
        target_resolved = target.resolve(strict=False)
        if (
            source_resolved == target_resolved
            or target_resolved.is_relative_to(source_resolved)
            or source_resolved.is_relative_to(target_resolved)
        ):
            raise WorkspaceRestoreError("restore_path_overlap")

        if target.exists():
            if not target.is_dir():
                raise WorkspaceRestoreError("restore_target_not_empty")
            try:
                next(target.iterdir())
            except StopIteration:
                pass
            else:
                raise WorkspaceRestoreError("restore_target_not_empty")
        return backup_directory, target

    @classmethod
    def _hash_file(cls, path: Path, code: str) -> str:
        digest = hashlib.sha256()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise WorkspaceRestoreError(code) from exc
        except OSError as exc:
            raise WorkspaceRestoreError("restore_file_io") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise WorkspaceRestoreError("restore_source_unsafe")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        finally:
            os.close(fd)
        return digest.hexdigest()

    def _read_manifest(
        self,
        backup_directory: Path,
    ) -> dict[str, object]:
        paths = WorkspacePaths(backup_directory)
        try:
            content = paths.read_regular(
                paths.path("manifest.json"),
                self._MANIFEST_LIMIT + 1,
            )
        except FileNotFoundError as exc:
            raise WorkspaceRestoreError("restore_manifest_missing") from exc
        except (OSError, StorageError) as exc:
            raise WorkspaceRestoreError("restore_manifest_invalid") from exc
        if len(content) > self._MANIFEST_LIMIT:
            raise WorkspaceRestoreError("restore_manifest_invalid")
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WorkspaceRestoreError("restore_manifest_invalid") from exc
        if not isinstance(payload, dict):
            raise WorkspaceRestoreError("restore_manifest_invalid")
        required = {
            "format_version",
            "run_id",
            "workspace_id",
            "created_at",
            "state",
            "database",
            "object_count",
            "objects",
            "integrity",
        }
        if set(payload) != required:
            raise WorkspaceRestoreError("restore_manifest_invalid")
        if (
            payload["format_version"] != 1
            or payload["state"] != "verified"
            or not isinstance(payload["run_id"], str)
            or not payload["run_id"]
            or not isinstance(payload["workspace_id"], str)
            or not payload["workspace_id"]
            or not isinstance(payload["created_at"], str)
            or not payload["created_at"]
        ):
            raise WorkspaceRestoreError("restore_manifest_invalid")
        integrity = payload["integrity"]
        if integrity != {
            "database": "ok",
            "objects": "ok",
            "hash_algorithm": "sha256",
        }:
            raise WorkspaceRestoreError("restore_manifest_invalid")
        database = payload["database"]
        if (
            not isinstance(database, dict)
            or set(database) != {"relative_path", "sha256"}
            or database["relative_path"] != "state/app.sqlite3"
            or not isinstance(database["sha256"], str)
            or self._HEX.fullmatch(database["sha256"]) is None
        ):
            raise WorkspaceRestoreError("restore_manifest_invalid")
        objects = payload["objects"]
        count = payload["object_count"]
        if (
            not isinstance(objects, list)
            or type(count) is not int
            or count < 0
            or count != len(objects)
        ):
            raise WorkspaceRestoreError("restore_manifest_invalid")
        return payload

    @staticmethod
    def _manifest_object(ref: ObjectRef) -> dict[str, object]:
        return {
            "object_id": ref.object_id,
            "content_sha256": ref.content_sha256,
            "relative_path": ref.relative_path,
            "kind": ref.kind,
            "media_type": ref.media_type,
            "byte_size": ref.byte_size,
            "retention_policy": ref.retention_policy,
        }

    def _validate_snapshot(
        self,
        backup_directory: Path,
        manifest: dict[str, object],
    ) -> tuple[Path, str, int, tuple[ObjectRef, ...]]:
        paths = WorkspacePaths(backup_directory)
        database = paths.path("state/app.sqlite3")
        database_spec = manifest["database"]
        if not isinstance(database_spec, dict):
            raise WorkspaceRestoreError("restore_manifest_invalid")
        expected_hash = database_spec["sha256"]
        if self._hash_file(database, "restore_database_missing") != expected_hash:
            raise WorkspaceRestoreError("restore_database_corrupt")

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{database.as_uri()}?mode=ro",
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise WorkspaceRestoreError("restore_database_invalid")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise WorkspaceRestoreError("restore_database_corrupt")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise WorkspaceRestoreError("restore_foreign_key_violation")

            migrations = tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT version,name,sha256 "
                    "FROM schema_migrations ORDER BY version"
                ).fetchall()
            )
            if migrations != self._expected_migrations:
                raise WorkspaceRestoreError("restore_migration_drift")

            workspace = connection.execute(
                "SELECT workspace_id,epoch "
                "FROM workspace_metadata WHERE singleton=1"
            ).fetchone()
            if (
                workspace is None
                or not workspace["workspace_id"]
                or type(workspace["epoch"]) is not int
                or workspace["epoch"] < 1
            ):
                raise WorkspaceRestoreError("restore_database_invalid")
            if workspace["workspace_id"] != manifest["workspace_id"]:
                raise WorkspaceRestoreError("restore_manifest_mismatch")

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
                raise WorkspaceRestoreError("restore_object_unavailable")
            expected_objects = [
                self._manifest_object(ref)
                for ref in refs
            ]
            if (
                manifest["objects"] != expected_objects
                or manifest["object_count"] != len(refs)
            ):
                raise WorkspaceRestoreError("restore_manifest_mismatch")
            return (
                database,
                str(workspace["workspace_id"]),
                int(workspace["epoch"]),
                refs,
            )
        except WorkspaceRestoreError:
            raise
        except StorageError as exc:
            raise WorkspaceRestoreError("restore_manifest_mismatch") from exc
        except sqlite3.Error as exc:
            raise WorkspaceRestoreError("restore_database_invalid") from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _copy_regular(
        source: Path,
        destination: Path,
        *,
        expected_sha256: str,
        expected_size: int | None,
        missing_code: str,
        corrupt_code: str,
    ) -> None:
        source_fd: int | None = None
        destination_fd: int | None = None
        try:
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                raise WorkspaceRestoreError("restore_source_unsafe")
            destination_fd = os.open(
                destination,
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
            if digest.hexdigest() != expected_sha256:
                raise WorkspaceRestoreError(corrupt_code)
            if expected_size is not None and byte_size != expected_size:
                raise WorkspaceRestoreError(corrupt_code)
        except FileNotFoundError as exc:
            raise WorkspaceRestoreError(missing_code) from exc
        except WorkspaceRestoreError:
            raise
        except OSError as exc:
            raise WorkspaceRestoreError("restore_file_io") from exc
        finally:
            if source_fd is not None:
                os.close(source_fd)
            if destination_fd is not None:
                os.close(destination_fd)

    def _stage_restore(
        self,
        backup_directory: Path,
        target: Path,
        *,
        database: Path,
        database_sha256: str,
        refs: tuple[ObjectRef, ...],
    ) -> Path:
        try:
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{target.name}.restore-",
                    dir=target.parent,
                )
            )
        except OSError as exc:
            raise WorkspaceRestoreError("restore_file_io") from exc

        staging_paths = WorkspacePaths(staging)
        try:
            state = staging_paths.directory("state")
            target_database = staging_paths.path("state/app.sqlite3")
            self._copy_regular(
                database,
                target_database,
                expected_sha256=database_sha256,
                expected_size=None,
                missing_code="restore_database_missing",
                corrupt_code="restore_database_corrupt",
            )
            WorkspacePaths.sync_directory(state)

            source_paths = WorkspacePaths(backup_directory)
            for ref in refs:
                destination_parent = staging_paths.directory(
                    Path(ref.relative_path).parent.as_posix()
                )
                self._copy_regular(
                    source_paths.path(ref.relative_path),
                    staging_paths.path(ref.relative_path),
                    expected_sha256=ref.content_sha256,
                    expected_size=ref.byte_size,
                    missing_code="restore_object_missing",
                    corrupt_code="restore_object_corrupt",
                )
                WorkspacePaths.sync_directory(destination_parent)
            return staging
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _verify_staging(
        self,
        staging: Path,
        *,
        workspace_id: str,
        epoch: int,
        refs: tuple[ObjectRef, ...],
    ) -> None:
        schema = SqliteSchemaConnectionFactory(staging, self._migrations)
        connection = schema.connect()
        try:
            identity = connection.execute(
                "SELECT workspace_id,epoch "
                "FROM workspace_metadata WHERE singleton=1"
            ).fetchone()
            if (
                identity is None
                or identity["workspace_id"] != workspace_id
                or identity["epoch"] != epoch
            ):
                raise WorkspaceRestoreError("restore_readback_mismatch")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise WorkspaceRestoreError("restore_database_corrupt")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise WorkspaceRestoreError("restore_foreign_key_violation")
        finally:
            connection.close()

        files = FilesystemObjectBytesAdapter(staging)
        uow = SqliteObjectUnitOfWorkAdapter(
            SqliteConnectionFactory(staging)
        )
        reader = ReadObject(files, uow)
        for ref in refs:
            try:
                reader(ref.object_id)
            except StorageError as exc:
                code = (
                    "restore_object_missing"
                    if exc.code in {"missing", "not_found"}
                    else "restore_object_corrupt"
                )
                raise WorkspaceRestoreError(code) from exc

    @staticmethod
    def _publish_staging(staging: Path, target: Path) -> None:
        target_existed = target.exists()
        try:
            if target_existed:
                if target.is_symlink() or not target.is_dir():
                    raise WorkspaceRestoreError("restore_target_unsafe")
                try:
                    next(target.iterdir())
                except StopIteration:
                    pass
                else:
                    raise WorkspaceRestoreError("restore_target_not_empty")
                target.rmdir()
            os.replace(staging, target)
            WorkspacePaths.sync_directory(target.parent)
        except WorkspaceRestoreError:
            raise
        except OSError as exc:
            if target_existed and not target.exists():
                try:
                    target.mkdir(mode=0o700)
                    WorkspacePaths.sync_directory(target.parent)
                except OSError:
                    pass
            raise WorkspaceRestoreError("restore_publish_failed") from exc

    def restore(
        self,
        backup_directory: Path,
        target: Path,
    ) -> WorkspaceRestoreResult:
        backup_directory, target = self._validate_paths(
            backup_directory,
            target,
        )
        manifest = self._read_manifest(backup_directory)
        database, workspace_id, epoch, refs = self._validate_snapshot(
            backup_directory,
            manifest,
        )
        database_spec = manifest["database"]
        if not isinstance(database_spec, dict):
            raise WorkspaceRestoreError("restore_manifest_invalid")

        staging: Path | None = None
        try:
            staging = self._stage_restore(
                backup_directory,
                target,
                database=database,
                database_sha256=str(database_spec["sha256"]),
                refs=refs,
            )
            self._verify_staging(
                staging,
                workspace_id=workspace_id,
                epoch=epoch,
                refs=refs,
            )
            self._publish_staging(staging, target)
            staging = None
            return WorkspaceRestoreResult(
                workspace_id=workspace_id,
                epoch=epoch,
                object_count=len(refs),
                state="restored",
            )
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

