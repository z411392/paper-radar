import sqlite3
from collections.abc import Callable

from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan
from libs.discovery.dtos.crossref_recovery import CrossrefRecoveredAttempt
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_capture_recovery_error import (
    CrossrefCaptureRecoveryError,
)
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError
from libs.discovery.ports.crossref_capture_store_port import CrossrefCaptureStorePort
from libs.discovery.ports.crossref_rate_gate_port import CrossrefRateGatePort
from libs.kernel.ports.read_object_port import ReadObjectPort


class SqliteCrossrefOrphanReceiptRecoveryAdapter:
    """Bounded orphan scan. Ambiguity or incomplete scan is visible, never guessed."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        read_object: ReadObjectPort,
        captures: CrossrefCaptureStorePort,
        gate: CrossrefRateGatePort,
        *,
        maximum_scan: int = 512,
    ) -> None:
        if type(maximum_scan) is not int or not 1 <= maximum_scan <= 4096:
            raise CrossrefCaptureRecoveryError(
                "invalid_crossref_orphan_scan_limit"
            )
        self._connect = connect
        self._read = read_object
        self._captures = captures
        self._gate = gate
        self._limit = maximum_scan

    @staticmethod
    def _candidate_attempt_key(content: bytes) -> str | None:
        try:
            payload = CrossrefCaptureRules.load_receipt(content)
        except CrossrefCaptureError:
            return None
        value = payload.get("attempt_key")
        return value if isinstance(value, str) else None

    def _candidates(
        self,
        page_id: str,
        request: CrossrefPageRequest,
    ) -> tuple[str, ...]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            page = connection.execute(
                "SELECT created_at,request_fingerprint FROM crossref_harvest_pages "
                "WHERE id=?",
                (page_id,),
            ).fetchone()
            if page is None:
                raise CrossrefCaptureRecoveryError("crossref_page_missing")
            if page["request_fingerprint"] != request.request_fingerprint:
                raise CrossrefCaptureRecoveryError(
                    "crossref_orphan_request_mismatch"
                )
            rows = connection.execute(
                "SELECT o.object_id FROM object_registry o "
                "WHERE o.kind='raw' AND o.state='available' "
                "AND julianday(o.created_at)>=julianday(?) "
                "AND NOT EXISTS("
                "SELECT 1 FROM crossref_harvest_page_attempts a "
                "WHERE a.receipt_id=o.object_id"
                ") "
                "ORDER BY julianday(o.created_at),o.object_id LIMIT ?",
                (page["created_at"], self._limit + 1),
            ).fetchall()
            connection.commit()
        except CrossrefCaptureRecoveryError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise CrossrefCaptureRecoveryError(
                "crossref_orphan_scan_database_error"
            ) from exc
        finally:
            if connection is not None:
                connection.close()
        if len(rows) > self._limit:
            raise CrossrefCaptureRecoveryError(
                "crossref_orphan_scan_incomplete"
            )
        matches = []
        for row in rows:
            object_id = row["object_id"]
            try:
                content = self._read(object_id)
            except Exception as exc:
                raise CrossrefCaptureRecoveryError(
                    "crossref_orphan_object_unreadable"
                ) from exc
            if self._candidate_attempt_key(content) == page_id:
                matches.append(object_id)
        return tuple(matches)

    def find(
        self,
        plan: CrossrefWindowPlan,
        page_id: str,
        request: CrossrefPageRequest,
    ) -> CrossrefRecoveredAttempt | None:
        matches = self._candidates(page_id, request)
        if not matches:
            return None
        if len(matches) != 1:
            raise CrossrefCaptureRecoveryError(
                "crossref_orphan_receipt_ambiguous"
            )
        receipt_id = matches[0]
        try:
            stored = self._captures.read(receipt_id)
        except CrossrefCaptureError as exc:
            raise CrossrefCaptureRecoveryError(
                "crossref_orphan_receipt_invalid"
            ) from exc
        if (
            stored.attempt_key != page_id
            or stored.request != request
        ):
            raise CrossrefCaptureRecoveryError(
                "crossref_orphan_request_mismatch"
            )
        try:
            with self._gate.slot(plan.definition.contact_email) as lease:
                decision = lease.observe(
                    stored.capture.status,
                    stored.capture.headers,
                    capture_error=stored.capture.capture_error,
                )
        except CrossrefRateError as exc:
            if exc.code == "crossref_circuit_open":
                return CrossrefRecoveredAttempt(
                    receipt_id,
                    "stop",
                    "crossref_circuit_open",
                )
            raise CrossrefCaptureRecoveryError(
                "crossref_orphan_gate_reconcile_failed"
            ) from exc
        return CrossrefRecoveredAttempt(
            receipt_id,
            decision.action,
            decision.failure_code,
        )
