import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from libs.scholarly_catalog.domain.services.evidence_snapshot_rules import EvidenceSnapshotRules
from libs.scholarly_catalog.dtos.evidence_anchor import EvidenceAnchor
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import EvidenceSnapshotError


class SqliteEvidenceSnapshotStoreAdapter:
    _LEVELS = {"abstract_only", "selected_sections", "full_text"}

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            if connection.in_transaction:
                raise EvidenceSnapshotError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise EvidenceSnapshotError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with connection:
                yield connection
        except sqlite3.IntegrityError as exc:
            raise EvidenceSnapshotError("evidence_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "evidence_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "evidence_database_error"
            )
            raise EvidenceSnapshotError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _snapshot_id(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"snapshot:[0-9a-f]{64}", value) is None:
            raise EvidenceSnapshotError("invalid_evidence_snapshot")
        return value

    @staticmethod
    def _structure(snapshot: EvidenceSnapshot) -> None:
        # No file I/O in the catalog transaction. Public preparation/readback
        # also use verify() to bind each quote to the immutable object bytes.
        EvidenceSnapshotRules.validate_metadata(snapshot)

    @staticmethod
    def _require_revision(
        connection: sqlite3.Connection,
        revision_id: str,
        work_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT 1 FROM paper_revisions WHERE id=? AND work_id=?",
            (revision_id, work_id),
        ).fetchone()
        if row is None:
            raise EvidenceSnapshotError("revision_missing")

    @staticmethod
    def _require_object(
        connection: sqlite3.Connection,
        object_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT state,kind,content_sha256,relative_path,byte_size FROM object_registry WHERE object_id=?",
            (object_id,),
        ).fetchone()
        if row is None:
            raise EvidenceSnapshotError("evidence_object_missing")
        if row["state"] != "available":
            raise EvidenceSnapshotError("evidence_object_unavailable")
        kind, digest = object_id.split(":", 1)
        if (
            row["kind"] != kind
            or row["content_sha256"] != digest
            or row["relative_path"] != f"objects/{kind}/{digest[:2]}/{digest}"
            or type(row["byte_size"]) is not int
            or row["byte_size"] <= 0
        ):
            raise EvidenceSnapshotError("evidence_object_metadata_mismatch")

    @staticmethod
    def _row_matches(snapshot: EvidenceSnapshot, row: sqlite3.Row) -> bool:
        return (
            row["id"] == snapshot.snapshot_id
            and row["revision_id"] == snapshot.revision_id
            and row["work_id"] == snapshot.work_id
            and row["object_id"] == snapshot.object_id
            and row["text_object_id"] == snapshot.text_object_id
            and row["parser_version"] == snapshot.parser_version
            and row["evidence_level"] == snapshot.evidence_level
            and row["coverage_json"] == snapshot.coverage_json
            and row["fingerprint"] == snapshot.fingerprint
        )

    @staticmethod
    def _anchor_matches(anchor: EvidenceAnchor, row: sqlite3.Row) -> bool:
        return (
            row["id"] == anchor.anchor_id
            and row["snapshot_id"] == anchor.snapshot_id
            and row["section_label"] == anchor.section_label
            and row["paragraph_id"] == anchor.paragraph_id
            and row["quote"] == anchor.quote
            and row["offset_start"] == anchor.offset_start
            and row["offset_end"] == anchor.offset_end
            and row["table_locator_json"] == anchor.table_locator_json
        )

    @classmethod
    def _read_in(
        cls,
        connection: sqlite3.Connection,
        snapshot_id: str,
    ) -> EvidenceSnapshot:
        row = connection.execute(
            "SELECT * FROM evidence_snapshots WHERE id=?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise EvidenceSnapshotError("evidence_snapshot_missing")
        anchor_rows = connection.execute(
            "SELECT * FROM evidence_anchors WHERE snapshot_id=? "
            "ORDER BY offset_start,offset_end,id",
            (snapshot_id,),
        ).fetchall()
        if not anchor_rows:
            raise EvidenceSnapshotError("evidence_snapshot_corrupt")
        anchors = tuple(
            EvidenceAnchor(
                item["id"],
                item["snapshot_id"],
                item["section_label"],
                item["paragraph_id"],
                item["quote"],
                item["offset_start"],
                item["offset_end"],
                item["table_locator_json"],
            )
            for item in anchor_rows
        )
        snapshot = EvidenceSnapshot(
            row["id"],
            row["revision_id"],
            row["work_id"],
            row["object_id"],
            row["text_object_id"],
            row["parser_version"],
            row["evidence_level"],
            row["coverage_json"],
            row["fingerprint"],
            row["created_at"],
            anchors,
        )
        cls._structure(snapshot)
        cls._require_revision(connection, snapshot.revision_id, snapshot.work_id)
        cls._require_object(connection, snapshot.object_id)
        cls._require_object(connection, snapshot.text_object_id)
        return snapshot

    def save(self, snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
        self._structure(snapshot)
        with self._transaction(write=True) as connection:
            self._require_revision(connection, snapshot.revision_id, snapshot.work_id)
            self._require_object(connection, snapshot.object_id)
            self._require_object(connection, snapshot.text_object_id)

            by_id = connection.execute(
                "SELECT * FROM evidence_snapshots WHERE id=?",
                (snapshot.snapshot_id,),
            ).fetchone()
            by_fingerprint = connection.execute(
                "SELECT * FROM evidence_snapshots WHERE fingerprint=?",
                (snapshot.fingerprint,),
            ).fetchone()
            existing = by_id or by_fingerprint
            if existing is None:
                connection.execute(
                    "INSERT INTO evidence_snapshots("
                    "id,revision_id,work_id,object_id,text_object_id,parser_version,"
                    "evidence_level,coverage_json,fingerprint,created_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        snapshot.snapshot_id,
                        snapshot.revision_id,
                        snapshot.work_id,
                        snapshot.object_id,
                        snapshot.text_object_id,
                        snapshot.parser_version,
                        snapshot.evidence_level,
                        snapshot.coverage_json,
                        snapshot.fingerprint,
                        snapshot.created_at,
                    ),
                )
            elif not self._row_matches(snapshot, existing):
                raise EvidenceSnapshotError("evidence_snapshot_conflict")

            for anchor in snapshot.anchors:
                row = connection.execute(
                    "SELECT * FROM evidence_anchors WHERE id=?",
                    (anchor.anchor_id,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO evidence_anchors("
                        "id,snapshot_id,section_label,paragraph_id,quote,"
                        "offset_start,offset_end,table_locator_json"
                        ") VALUES(?,?,?,?,?,?,?,?)",
                        (
                            anchor.anchor_id,
                            anchor.snapshot_id,
                            anchor.section_label,
                            anchor.paragraph_id,
                            anchor.quote,
                            anchor.offset_start,
                            anchor.offset_end,
                            anchor.table_locator_json,
                        ),
                    )
                elif not self._anchor_matches(anchor, row):
                    raise EvidenceSnapshotError("evidence_anchor_conflict")

            return self._read_in(connection, snapshot.snapshot_id)

    def read(self, snapshot_id: str) -> EvidenceSnapshot:
        snapshot_id = self._snapshot_id(snapshot_id)
        with self._transaction(write=False) as connection:
            return self._read_in(connection, snapshot_id)
