"""Local pre-send authority only; dispatching requires later receipt reconciliation.

No network or object I/O is performed. Existing unclaimed pages require legacy
recovery; absence of an attempt row does not prove absence of an orphan receipt.
"""

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan
from libs.discovery.exceptions.crossref_capture_claim_error import CrossrefCaptureClaimError as Error
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class SqliteCrossrefCaptureClaimStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _text(value: object) -> str:
        if (not isinstance(value, str) or not value or value != value.strip()
                or len(value) > 512 or any(ord(c) < 32 for c in value)):
            raise Error("invalid_crossref_claim_identity")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise Error("invalid_crossref_claim_identity") from None
        return value

    @staticmethod
    def _us(value: datetime) -> int:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise Error("invalid_crossref_claim_time")
        try:
            delta = value.astimezone(timezone.utc) - EPOCH
            return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        except (ValueError, OverflowError):
            raise Error("invalid_crossref_claim_time") from None

    @staticmethod
    def _hash(*parts: object) -> str:
        return hashlib.sha256(json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise Error("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise Error("foreign_keys_required")
            if write:
                if connection.execute("PRAGMA journal_mode").fetchone()[0] not in {
                    "wal", "delete", "truncate", "persist",
                }:
                    raise Error("crossref_claim_durable_journal_required")
                connection.execute("PRAGMA synchronous=EXTRA")
            else:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xff
            raise Error("crossref_claim_database_busy" if code in {5, 6}
                        else "crossref_claim_database_error") from exc
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()

    @staticmethod
    def _workspace(connection: sqlite3.Connection, workspace_id: str, epoch: int) -> None:
        row = connection.execute("SELECT * FROM workspace_metadata WHERE singleton=1").fetchone()
        if row is None or row["workspace_id"] != workspace_id or row["epoch"] != epoch:
            raise Error("crossref_claim_workspace_changed")
        if row["external_effects_enabled"] != 1:
            raise Error("crossref_claim_effects_disabled")

    @staticmethod
    def _active(connection: sqlite3.Connection, pass_id: str, request: CrossrefPageRequest) -> sqlite3.Row:
        row = connection.execute(
            "SELECT p.*,w.binding_key,w.config_version,w.from_index,w.until_index,w.rows,"
            "w.query_fingerprint,w.state AS window_state FROM crossref_harvest_passes p "
            "JOIN crossref_harvest_windows w ON w.id=p.window_id WHERE p.id=?", (pass_id,),
        ).fetchone()
        if row is None:
            raise Error("crossref_claim_page_not_active")
        latest = connection.execute(
            "SELECT MAX(pass_no) FROM crossref_harvest_passes WHERE window_id=?", (row["window_id"],),
        ).fetchone()[0]
        if (row["state"] != "running" or row["finished_at"] is not None
                or row["pass_no"] != latest or row["current_cursor"] != request.cursor
                or row["query_fingerprint"] != request.query_fingerprint
                or row["parameters_fingerprint"] != request.parameters_fingerprint):
            raise Error("crossref_claim_page_not_active")
        if connection.execute("SELECT 1 FROM crossref_window_splits WHERE parent_window_id=?",
                              (row["window_id"],)).fetchone() is not None:
            raise Error("crossref_claim_page_not_active")
        repairs = connection.execute(
            "SELECT state,finished_at FROM crossref_repair_runs WHERE pass_id=? AND window_id=?",
            (pass_id, row["window_id"]),
        ).fetchall()
        if repairs:
            if len(repairs) != 1 or repairs[0]["state"] != "running" or repairs[0]["finished_at"] is not None:
                raise Error("crossref_claim_page_not_active")
        elif row["window_state"] != "running":
            raise Error("crossref_claim_page_not_active")
        return row

    @staticmethod
    def _decode(row: sqlite3.Row) -> CrossrefCaptureClaim:
        try:
            data = json.loads(row["request_json"])
            request = CrossrefPageRequest(**data)
            CrossrefCaptureRules.request_shape(request)
            if CrossrefCaptureRules.canonical(data).decode() != row["request_json"]:
                raise ValueError("noncanonical request")
            return CrossrefCaptureClaim(
                row["id"], row["page_id"], row["pass_id"], row["fencing_token"], row["owner_id"],
                row["workspace_id"], row["workspace_epoch"], request,
                EPOCH + timedelta(microseconds=row["reserved_us"]),
                EPOCH + timedelta(microseconds=row["lease_until_us"]), row["state"],
            )
        except (ValueError, TypeError, OverflowError, CrossrefCaptureError) as exc:
            raise Error("crossref_claim_state_corrupt") from exc

    def reserve(
        self, plan: CrossrefWindowPlan, pass_id: str, request: CrossrefPageRequest, *,
        owner_id: str, expected_workspace_id: str, expected_epoch: int,
        now: datetime, lease_seconds: int,
    ) -> CrossrefCaptureClaim:
        self._text(pass_id)
        self._text(owner_id)
        self._text(expected_workspace_id)
        if type(expected_epoch) is not int or not 1 <= expected_epoch < 2**63:
            raise Error("invalid_crossref_claim_epoch")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
            raise Error("invalid_crossref_claim_lease")
        current = self._us(now)
        try:
            expires = self._us(now + timedelta(seconds=lease_seconds))
        except OverflowError:
            raise Error("invalid_crossref_claim_time") from None
        try:
            if not isinstance(request, CrossrefPageRequest) or CrossrefSourceAdapter().page(
                plan, request.cursor,
            ) != request:
                raise Error("crossref_claim_request_mismatch")
        except (CrossrefProtocolError, CrossrefCaptureError) as exc:
            raise Error("crossref_claim_request_mismatch") from exc
        request_json = CrossrefCaptureRules.canonical(asdict(request)).decode()
        with self._transaction() as connection:
            self._workspace(connection, expected_workspace_id, expected_epoch)
            state = self._active(connection, pass_id, request)
            definition = plan.definition
            if (state["binding_key"], state["config_version"], state["rows"],
                state["from_index"], state["until_index"]) != (
                definition.binding_key, definition.config_version, definition.rows,
                definition.from_index.astimezone(timezone.utc).isoformat(),
                definition.until_index.astimezone(timezone.utc).isoformat(),
            ):
                raise Error("crossref_claim_request_mismatch")
            page = connection.execute(
                "SELECT * FROM crossref_harvest_pages WHERE pass_id=? AND page_no=?",
                (pass_id, state["next_page_no"]),
            ).fetchone()
            page_id = "crossref-page:" + self._hash(pass_id, state["next_page_no"], request.request_fingerprint)
            latest = None
            if page is not None:
                latest = connection.execute(
                    "SELECT * FROM crossref_capture_claims WHERE page_id=? ORDER BY fencing_token DESC LIMIT 1",
                    (page["id"],),
                ).fetchone()
                if latest is None:
                    raise Error("crossref_claim_legacy_page_requires_recovery")
                if (page["id"] != page_id or page["state"] != "requested"
                        or page["successful_receipt_id"] is not None
                        or page["cursor_in"] != request.cursor
                        or page["request_fingerprint"] != request.request_fingerprint):
                    raise Error("crossref_claim_page_not_active")
            if page is not None:
                self._unreceived(connection, page_id)
            if latest is not None:
                if latest["state"] == "dispatching":
                    raise Error("crossref_claim_outcome_unknown")
                old = self._decode(latest)
                if old.workspace_id != expected_workspace_id or old.workspace_epoch != expected_epoch:
                    raise Error("crossref_claim_recovery_required")
                if old.request != request:
                    raise Error("crossref_claim_request_mismatch")
                if current < max(latest["reserved_us"], latest["ended_us"] or latest["reserved_us"]):
                    raise Error("crossref_claim_clock_regressed")
                if latest["state"] == "reserved":
                    if current < latest["lease_until_us"]:
                        if old.owner_id != owner_id:
                            raise Error("crossref_claim_busy")
                        return old
                    connection.execute(
                        "UPDATE crossref_capture_claims SET state='expired',ended_us=? WHERE id=?",
                        (current, old.claim_id),
                    )
            else:
                connection.execute(
                    "INSERT INTO crossref_harvest_pages "
                    "(id,pass_id,page_no,cursor_in,request_fingerprint,state,created_at) "
                    "VALUES(?,?,?,?,?,'requested',?)",
                    (page_id, pass_id, state["next_page_no"], request.cursor,
                     request.request_fingerprint, now.astimezone(timezone.utc).isoformat()),
                )
            token = 1 if latest is None else latest["fencing_token"] + 1
            if token >= 2**63:
                raise Error("crossref_claim_token_exhausted")
            claim_id = "crossref-capture-claim:" + self._hash(page_id, token, expected_workspace_id, expected_epoch)
            connection.execute(
                "INSERT INTO crossref_capture_claims "
                "(id,page_id,pass_id,fencing_token,owner_id,workspace_id,workspace_epoch,request_json,"
                "reserved_us,lease_until_us,state) VALUES(?,?,?,?,?,?,?,?,?,?,'reserved')",
                (claim_id, page_id, pass_id, token, owner_id, expected_workspace_id, expected_epoch,
                 request_json, current, expires),
            )
            return self._decode(connection.execute(
                "SELECT * FROM crossref_capture_claims WHERE id=?", (claim_id,),
            ).fetchone())

    @staticmethod
    def _unreceived(connection: sqlite3.Connection, page_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM crossref_harvest_page_attempts WHERE page_id=? LIMIT 1", (page_id,),
        ).fetchone() is not None:
            raise Error("crossref_claim_recovery_required")

    def _owned(self, connection: sqlite3.Connection, claim: CrossrefCaptureClaim) -> sqlite3.Row:
        if (not isinstance(claim, CrossrefCaptureClaim)
                or type(claim.fencing_token) is not int or claim.fencing_token < 1
                or type(claim.workspace_epoch) is not int or claim.workspace_epoch < 1):
            raise Error("invalid_crossref_claim_identity")
        row = connection.execute("SELECT * FROM crossref_capture_claims WHERE id=?", (claim.claim_id,)).fetchone()
        if row is None or replace(self._decode(row), state=claim.state) != claim:
            raise Error("crossref_claim_fenced")
        latest = connection.execute(
            "SELECT MAX(fencing_token) FROM crossref_capture_claims WHERE page_id=?", (claim.page_id,),
        ).fetchone()[0]
        if latest != claim.fencing_token:
            raise Error("crossref_claim_fenced")
        return row

    def begin_dispatch(self, claim: CrossrefCaptureClaim, *, now: datetime) -> None:
        current = self._us(now)
        with self._transaction() as connection:
            row = self._owned(connection, claim)
            if row["state"] == "dispatching":
                raise Error("crossref_claim_dispatch_already_started")
            if row["state"] != "reserved":
                raise Error("crossref_claim_fenced")
            if current < row["reserved_us"]:
                raise Error("crossref_claim_clock_regressed")
            if current >= row["lease_until_us"]:
                raise Error("crossref_claim_expired")
            self._workspace(connection, claim.workspace_id, claim.workspace_epoch)
            state = self._active(connection, claim.pass_id, claim.request)
            page = connection.execute("SELECT * FROM crossref_harvest_pages WHERE id=?", (claim.page_id,)).fetchone()
            if (page is None or page["state"] != "requested" or page["pass_id"] != claim.pass_id
                    or page["page_no"] != state["next_page_no"] or page["successful_receipt_id"] is not None
                    or page["cursor_in"] != claim.request.cursor
                    or page["request_fingerprint"] != claim.request.request_fingerprint):
                raise Error("crossref_claim_page_not_active")
            self._unreceived(connection, claim.page_id)
            connection.execute(
                "UPDATE crossref_capture_claims SET state='dispatching',dispatched_us=? "
                "WHERE id=? AND state='reserved'", (current, claim.claim_id),
            )

    def release(self, claim: CrossrefCaptureClaim, *, now: datetime) -> None:
        current = self._us(now)
        with self._transaction() as connection:
            row = self._owned(connection, claim)
            if row["state"] == "released":
                return
            if row["state"] == "dispatching":
                raise Error("crossref_claim_dispatch_already_started")
            if row["state"] != "reserved":
                raise Error("crossref_claim_fenced")
            if current < row["reserved_us"]:
                raise Error("crossref_claim_clock_regressed")
            connection.execute("UPDATE crossref_capture_claims SET state='released',ended_us=? WHERE id=?",
                               (current, claim.claim_id))

    def read(self, claim_id: str) -> CrossrefCaptureClaim:
        self._text(claim_id)
        with self._transaction(write=False) as connection:
            row = connection.execute("SELECT * FROM crossref_capture_claims WHERE id=?", (claim_id,)).fetchone()
            if row is None:
                raise Error("crossref_claim_missing")
            return self._decode(row)
