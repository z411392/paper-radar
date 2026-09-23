import sqlite3
from collections.abc import Callable

from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import EvidenceSnapshotError


class SqliteEvidenceSnapshotStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def save(self, snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
        raise EvidenceSnapshotError("not_implemented")

    def read(self, snapshot_id: str) -> EvidenceSnapshot:
        raise EvidenceSnapshotError("not_implemented")
