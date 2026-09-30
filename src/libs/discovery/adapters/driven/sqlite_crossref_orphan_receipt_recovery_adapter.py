"""Conservative bounded recovery, not an atomic authorization for a later HTTP call."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules
from libs.discovery.dtos.crossref_capture import CrossrefStoredCapture
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan
from libs.discovery.dtos.crossref_recovery import CrossrefRecoveredAttempt
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_capture_recovery_error import CrossrefCaptureRecoveryError
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError
from libs.discovery.ports.crossref_capture_store_port import CrossrefCaptureStorePort
from libs.discovery.ports.crossref_rate_gate_port import CrossrefRateGatePort
from libs.kernel.ports.read_object_port import ReadObjectPort


@dataclass(frozen=True)
class _ScanObject:
    object_id: str
    content_sha256: str
    byte_size: int
    state: str


class SqliteCrossrefOrphanReceiptRecoveryAdapter:
    """Incomplete knowledge never means absence; no object or journal state is changed.

    Without a durable receipt index, inspect all unlinked raw registry entries within
    both budgets. Timestamps are not publication sequence numbers. Unavailable entries
    cannot be silently excluded. Budget exhaustion requires explicit operator recovery.
    """

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        read_object: ReadObjectPort,
        captures: CrossrefCaptureStorePort,
        gate: CrossrefRateGatePort,
        *,
        maximum_scan: int = 512,
        maximum_scan_bytes: int = 64_000_000,
    ) -> None:
        if type(maximum_scan) is not int or not 1 <= maximum_scan <= 4096:
            raise CrossrefCaptureRecoveryError("invalid_crossref_orphan_scan_limit")
        if type(maximum_scan_bytes) is not int or not 1 <= maximum_scan_bytes <= 512_000_000:
            raise CrossrefCaptureRecoveryError("invalid_crossref_orphan_scan_byte_limit")
        self._connect = connect
        self._read = read_object
        self._captures = captures
        self._gate = gate
        self._limit = maximum_scan
        self._byte_limit = maximum_scan_bytes

    @staticmethod
    def _candidate_attempt_key(content: bytes) -> str | None:
        try:
            payload = CrossrefCaptureRules.load_receipt(content)
        except CrossrefCaptureError:
            return None  # Raw response bytes are not necessarily receipt JSON.
        value = payload.get("attempt_key")
        return value if isinstance(value, str) else None

    @staticmethod
    def _active_repair(connection: sqlite3.Connection, page: sqlite3.Row) -> bool:
        # Repair generations deliberately preserve the preceding window's traversal state.
        # Schema v11 has no repair lane; v12+ must carry an explicit running repair relation.
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='crossref_repair_runs'"
        ).fetchone()
        if exists is None:
            return False
        return connection.execute(
            "SELECT 1 FROM crossref_repair_runs WHERE window_id=? AND pass_id=? "
            "AND state='running' AND finished_at IS NULL",
            (page["window_id"], page["pass_id"]),
        ).fetchone() is not None

    def _snapshot(self, page_id: str, request: CrossrefPageRequest) -> tuple[_ScanObject, ...]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise CrossrefCaptureRecoveryError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            page = connection.execute(
                "SELECT p.request_fingerprint,p.cursor_in,p.state,p.page_no,p.successful_receipt_id,"
                "x.state AS pass_state,x.current_cursor,x.next_page_no,"
                "x.parameters_fingerprint,x.id AS pass_id,x.pass_no,x.finished_at,"
                "w.id AS window_id,w.query_fingerprint,w.state AS window_state,"
                "(SELECT MAX(y.pass_no) FROM crossref_harvest_passes y "
                "WHERE y.window_id=x.window_id) AS latest_pass_no "
                "FROM crossref_harvest_pages p "
                "JOIN crossref_harvest_passes x ON x.id=p.pass_id "
                "JOIN crossref_harvest_windows w ON w.id=x.window_id WHERE p.id=?",
                (page_id,),
            ).fetchone()
            if page is None:
                raise CrossrefCaptureRecoveryError("crossref_page_missing")
            if (
                page["request_fingerprint"] != request.request_fingerprint
                or page["parameters_fingerprint"] != request.parameters_fingerprint
                or page["query_fingerprint"] != request.query_fingerprint
                or page["cursor_in"] != request.cursor
            ):
                raise CrossrefCaptureRecoveryError("crossref_orphan_request_mismatch")
            if (
                page["state"] != "requested" or page["pass_state"] != "running"
                or page["page_no"] != page["next_page_no"]
                or page["cursor_in"] != page["current_cursor"]
                or page["pass_no"] != page["latest_pass_no"]
                or page["finished_at"] is not None
                or page["successful_receipt_id"] is not None
            ):
                raise CrossrefCaptureRecoveryError("crossref_orphan_page_not_active")
            if page["window_state"] != "running" and not self._active_repair(connection, page):
                raise CrossrefCaptureRecoveryError("crossref_orphan_page_not_active")
            rows = connection.execute(
                "SELECT o.object_id,o.content_sha256,o.byte_size,o.state FROM object_registry o "
                "WHERE o.kind='raw' AND NOT EXISTS("
                "SELECT 1 FROM crossref_harvest_page_attempts a WHERE a.receipt_id=o.object_id"
                ") ORDER BY o.object_id LIMIT ?",
                (self._limit + 1,),
            ).fetchall()
            connection.commit()  # Filesystem I/O must happen outside this snapshot transaction.
        except CrossrefCaptureRecoveryError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise CrossrefCaptureRecoveryError("crossref_orphan_scan_database_error") from exc
        finally:
            if connection is not None:
                connection.close()
        if len(rows) > self._limit:
            raise CrossrefCaptureRecoveryError("crossref_orphan_scan_incomplete")
        result = []
        byte_count = 0
        for row in rows:
            try:
                object_id = CrossrefCaptureRules.object_id(row["object_id"])
                digest = CrossrefCaptureRules.digest(row["content_sha256"])
            except CrossrefCaptureError as exc:
                raise CrossrefCaptureRecoveryError("crossref_orphan_registry_corrupt") from exc
            size = row["byte_size"]
            if object_id != "raw:" + digest or type(size) is not int or size < 0:
                raise CrossrefCaptureRecoveryError("crossref_orphan_registry_corrupt")
            if row["state"] != "available":
                raise CrossrefCaptureRecoveryError("crossref_orphan_object_unreadable")
            byte_count += size
            if byte_count > self._byte_limit:
                raise CrossrefCaptureRecoveryError("crossref_orphan_scan_byte_limit")
            result.append(_ScanObject(object_id, digest, size, row["state"]))
        return tuple(result)

    def _unchanged(
        self, page_id: str, request: CrossrefPageRequest, snapshot: tuple[_ScanObject, ...],
    ) -> None:
        if self._snapshot(page_id, request) != snapshot:
            raise CrossrefCaptureRecoveryError("crossref_orphan_scan_changed")

    def find(
        self,
        plan: CrossrefWindowPlan,
        page_id: str,
        request: CrossrefPageRequest,
    ) -> CrossrefRecoveredAttempt | None:
        try:
            CrossrefCaptureRules.text(page_id, "crossref_orphan_request_mismatch")
            if not isinstance(request, CrossrefPageRequest):
                raise CrossrefCaptureError("crossref_orphan_request_mismatch")
            if CrossrefSourceAdapter().page(plan, request.cursor) != request:
                raise CrossrefCaptureError("crossref_orphan_request_mismatch")
        except (CrossrefCaptureError, CrossrefProtocolError) as exc:
            raise CrossrefCaptureRecoveryError("crossref_orphan_request_mismatch") from exc

        snapshot = self._snapshot(page_id, request)
        matches = []
        for entry in snapshot:
            try:
                content = self._read(entry.object_id)
                if (
                    not isinstance(content, bytes) or len(content) != entry.byte_size
                    or CrossrefCaptureRules.sha(content) != entry.content_sha256
                ):
                    raise CrossrefCaptureError("crossref_orphan_object_unreadable")
            except Exception as exc:
                raise CrossrefCaptureRecoveryError("crossref_orphan_object_unreadable") from exc
            if self._candidate_attempt_key(content) == page_id:
                matches.append(entry.object_id)
        self._unchanged(page_id, request, snapshot)
        if not matches:
            return None
        if len(matches) != 1:
            raise CrossrefCaptureRecoveryError("crossref_orphan_receipt_ambiguous")
        receipt_id = matches[0]
        try:
            stored = self._captures.read(receipt_id)
        except Exception as exc:
            raise CrossrefCaptureRecoveryError("crossref_orphan_receipt_invalid") from exc
        if (
            not isinstance(stored, CrossrefStoredCapture) or stored.receipt_id != receipt_id
            or stored.attempt_key != page_id or stored.request != request
        ):
            raise CrossrefCaptureRecoveryError("crossref_orphan_request_mismatch")
        self._unchanged(page_id, request, snapshot)
        try:
            with self._gate.slot(plan.definition.contact_email) as lease:
                decision = lease.observe(
                    stored.capture.status,
                    stored.capture.headers,
                    capture_error=stored.capture.capture_error,
                )
        except CrossrefRateError as exc:
            if exc.code == "crossref_circuit_open":
                self._unchanged(page_id, request, snapshot)
                return CrossrefRecoveredAttempt(receipt_id, "stop", "crossref_circuit_open")
            raise CrossrefCaptureRecoveryError("crossref_orphan_gate_reconcile_failed") from exc
        self._unchanged(page_id, request, snapshot)
        return CrossrefRecoveredAttempt(receipt_id, decision.action, decision.failure_code)
