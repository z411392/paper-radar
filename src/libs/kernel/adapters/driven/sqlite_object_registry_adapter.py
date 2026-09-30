import sqlite3

from libs.kernel.dtos.object_ref import ObjectRef


class SqliteObjectRegistryAdapter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @staticmethod
    def _decode(row: sqlite3.Row) -> ObjectRef:
        return ObjectRef(
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

    def get(self, object_id: str) -> ObjectRef | None:
        row = self._connection.execute(
            "SELECT * FROM object_registry WHERE object_id=?", (object_id,)
        ).fetchone()
        return self._decode(row) if row is not None else None

    def add(self, ref: ObjectRef) -> None:
        self._connection.execute(
            "INSERT INTO object_registry(object_id,content_sha256,relative_path,kind,media_type,byte_size,"
            "state,created_at,retention_policy) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                ref.object_id,
                ref.content_sha256,
                ref.relative_path,
                ref.kind,
                ref.media_type,
                ref.byte_size,
                ref.state,
                ref.created_at,
                ref.retention_policy,
            ),
        )

    def all(self) -> tuple[ObjectRef, ...]:
        rows = self._connection.execute("SELECT * FROM object_registry ORDER BY object_id").fetchall()
        return tuple(self._decode(row) for row in rows)
