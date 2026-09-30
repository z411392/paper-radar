import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.discovery.dtos.crossref_capture import CrossrefReplayedPage
from libs.discovery.dtos.crossref_harvest import (
    CrossrefHarvestPassState,
    CrossrefHarvestWindowState,
    CrossrefJournalPage,
    CrossrefPendingItem,
)
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan
from libs.discovery.exceptions.crossref_harvest_journal_error import (
    CrossrefHarvestJournalError,
)


class SqliteCrossrefHarvestJournalAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _fingerprint(value: object, code: str = "invalid_crossref_journal") -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise CrossrefHarvestJournalError(code)
        return value

    @staticmethod
    def _text(value: object, code: str, maximum: int = 65536) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\0" in value
        ):
            raise CrossrefHarvestJournalError(code)
        try:
            if len(value.encode("utf-8")) > maximum:
                raise CrossrefHarvestJournalError(code)
        except UnicodeEncodeError:
            raise CrossrefHarvestJournalError(code) from None
        return value

    @staticmethod
    def _time(value: object) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise CrossrefHarvestJournalError("invalid_crossref_journal_time")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise CrossrefHarvestJournalError("invalid_crossref_journal_time") from None

    @staticmethod
    def _hash(*parts: object) -> str:
        encoded = json.dumps(
            parts,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _plan_values(
        cls,
        plan: CrossrefWindowPlan,
    ) -> tuple[str, str, str, str, str, int]:
        if not isinstance(plan, CrossrefWindowPlan):
            raise CrossrefHarvestJournalError("invalid_crossref_plan")
        definition = plan.definition
        binding = cls._text(
            getattr(definition, "binding_key", None),
            "invalid_crossref_plan",
            512,
        )
        config = cls._text(
            getattr(definition, "config_version", None),
            "invalid_crossref_plan",
            128,
        )
        query = cls._fingerprint(plan.query_fingerprint, "invalid_crossref_plan")
        cls._fingerprint(plan.parameters_fingerprint, "invalid_crossref_plan")
        rows = getattr(definition, "rows", None)
        if type(rows) is not int or not 1 <= rows <= 1000:
            raise CrossrefHarvestJournalError("invalid_crossref_plan")
        start = cls._time(getattr(definition, "from_index", None))
        end = cls._time(getattr(definition, "until_index", None))
        if start >= end:
            raise CrossrefHarvestJournalError("invalid_crossref_plan")
        return binding, query, config, start, end, rows

    @classmethod
    def _window_id(cls, plan: CrossrefWindowPlan) -> str:
        cls._plan_values(plan)
        return "crossref-window:" + cls._hash(
            plan.definition.binding_key,
            plan.query_fingerprint,
        )

    @classmethod
    def _pass_id(
        cls,
        window_id: str,
        pass_no: int,
        parameters_fingerprint: str,
    ) -> str:
        return "crossref-pass:" + cls._hash(
            window_id,
            pass_no,
            parameters_fingerprint,
        )

    @classmethod
    def _page_id(
        cls,
        pass_id: str,
        page_no: int,
        request_fingerprint: str,
    ) -> str:
        return "crossref-page:" + cls._hash(
            pass_id,
            page_no,
            request_fingerprint,
        )

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise CrossrefHarvestJournalError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise CrossrefHarvestJournalError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except CrossrefHarvestJournalError:
            raise
        except sqlite3.IntegrityError as exc:
            raise CrossrefHarvestJournalError("crossref_journal_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "crossref_journal_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "crossref_journal_database_error"
            )
            raise CrossrefHarvestJournalError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _window_state(row: sqlite3.Row) -> CrossrefHarvestWindowState:
        return CrossrefHarvestWindowState(
            row["id"],
            row["state"],
            row["binding_key"],
            row["query_fingerprint"],
        )

    @staticmethod
    def _pass_state(row: sqlite3.Row) -> CrossrefHarvestPassState:
        return CrossrefHarvestPassState(
            row["id"],
            row["window_id"],
            row["pass_no"],
            row["parameters_fingerprint"],
            row["state"],
            row["current_cursor"],
            row["next_page_no"],
            bool(row["traversal_complete"]),
            bool(row["accounting_complete"]),
            row["first_reported_total"],
            row["raw_item_count"],
            row["processed_item_count"],
            row["quarantine_count"],
            row["unique_doi_count"],
            row["duplicate_doi_count"],
            row["parse_gap_count"],
            bool(row["drift_suspected"]),
            bool(row["repair_pending"]),
            row["source_completeness"],
            row["error_code"],
        )

    @staticmethod
    def _page_state(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> CrossrefJournalPage:
        attempt_count = connection.execute(
            "SELECT count(*) FROM crossref_harvest_page_attempts WHERE page_id=?",
            (row["id"],),
        ).fetchone()[0]
        return CrossrefJournalPage(
            row["id"],
            row["pass_id"],
            row["page_no"],
            row["cursor_in"],
            row["request_fingerprint"],
            row["state"],
            row["last_error_code"],
            row["successful_receipt_id"],
            attempt_count,
            row["cursor_out"],
        )

    def ensure_window(
        self,
        plan: CrossrefWindowPlan,
        created_at: datetime,
    ) -> CrossrefHarvestWindowState:
        binding, query, config, start, end, rows = self._plan_values(plan)
        created = self._time(created_at)
        window_id = self._window_id(plan)
        expected = (binding, query, config, start, end, rows)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM crossref_harvest_windows WHERE id=?",
                (window_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO crossref_harvest_windows("
                    "id,binding_key,query_fingerprint,config_version,from_index,"
                    "until_index,rows,state,created_at,updated_at"
                    ") VALUES(?,?,?,?,?,?,?,'pending',?,?)",
                    (window_id, *expected, created, created),
                )
                row = connection.execute(
                    "SELECT * FROM crossref_harvest_windows WHERE id=?",
                    (window_id,),
                ).fetchone()
            elif (
                row["binding_key"],
                row["query_fingerprint"],
                row["config_version"],
                row["from_index"],
                row["until_index"],
                row["rows"],
            ) != expected:
                raise CrossrefHarvestJournalError("crossref_window_conflict")
            assert row is not None
            return self._window_state(row)

    def read_window(self, window_id: str) -> CrossrefHarvestWindowState:
        self._text(window_id, "invalid_crossref_window_id", 256)
        with self._transaction(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM crossref_harvest_windows WHERE id=?",
                (window_id,),
            ).fetchone()
            if row is None:
                raise CrossrefHarvestJournalError("crossref_window_missing")
            return self._window_state(row)

    def start_pass(
        self,
        plan: CrossrefWindowPlan,
        started_at: datetime,
    ) -> CrossrefHarvestPassState:
        started = self._time(started_at)
        window_id = self._window_id(plan)
        parameters = self._fingerprint(
            plan.parameters_fingerprint,
            "invalid_crossref_plan",
        )
        with self._transaction(write=True) as connection:
            window = connection.execute(
                "SELECT * FROM crossref_harvest_windows WHERE id=?",
                (window_id,),
            ).fetchone()
            if window is None:
                raise CrossrefHarvestJournalError("crossref_window_missing")
            latest = connection.execute(
                "SELECT * FROM crossref_harvest_passes "
                "WHERE window_id=? ORDER BY pass_no DESC LIMIT 1",
                (window_id,),
            ).fetchone()
            if latest is not None and latest["state"] == "running":
                if latest["parameters_fingerprint"] != parameters:
                    raise CrossrefHarvestJournalError(
                        "crossref_pass_parameters_mismatch"
                    )
                return self._pass_state(latest)
            if latest is not None and latest["state"] == "completed":
                if latest["parameters_fingerprint"] != parameters:
                    raise CrossrefHarvestJournalError(
                        "crossref_pass_parameters_mismatch"
                    )
                return self._pass_state(latest)
            pass_no = 1 if latest is None else latest["pass_no"] + 1
            pass_id = self._pass_id(window_id, pass_no, parameters)
            connection.execute(
                "INSERT INTO crossref_harvest_passes("
                "id,window_id,pass_no,parameters_fingerprint,state,current_cursor,"
                "started_at"
                ") VALUES(?,?,?,?,'running','*',?)",
                (pass_id, window_id, pass_no, parameters, started),
            )
            connection.execute(
                "UPDATE crossref_harvest_windows SET state='running',updated_at=? "
                "WHERE id=?",
                (started, window_id),
            )
            row = connection.execute(
                "SELECT * FROM crossref_harvest_passes WHERE id=?",
                (pass_id,),
            ).fetchone()
            assert row is not None
            return self._pass_state(row)

    def read_pass(self, pass_id: str) -> CrossrefHarvestPassState:
        self._text(pass_id, "invalid_crossref_pass_id", 256)
        with self._transaction(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM crossref_harvest_passes WHERE id=?",
                (pass_id,),
            ).fetchone()
            if row is None:
                raise CrossrefHarvestJournalError("crossref_pass_missing")
            return self._pass_state(row)

    def fail_pass(
        self,
        pass_id: str,
        error_code: str,
        finished_at: datetime,
    ) -> CrossrefHarvestPassState:
        self._text(pass_id, "invalid_crossref_pass_id", 256)
        error = self._text(error_code, "invalid_crossref_pass_error", 128)
        finished = self._time(finished_at)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM crossref_harvest_passes WHERE id=?",
                (pass_id,),
            ).fetchone()
            if row is None:
                raise CrossrefHarvestJournalError("crossref_pass_missing")
            if row["state"] == "failed":
                if row["error_code"] != error:
                    raise CrossrefHarvestJournalError("crossref_pass_conflict")
                return self._pass_state(row)
            if row["state"] != "running":
                raise CrossrefHarvestJournalError("crossref_pass_conflict")
            connection.execute(
                "UPDATE crossref_harvest_passes SET state='failed',error_code=?,"
                "finished_at=? WHERE id=? AND state='running'",
                (error, finished, pass_id),
            )
            connection.execute(
                "UPDATE crossref_harvest_windows SET state='failed',updated_at=? "
                "WHERE id=?",
                (finished, row["window_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM crossref_harvest_passes WHERE id=?",
                (pass_id,),
            ).fetchone()
            assert updated is not None
            return self._pass_state(updated)

    def begin_page(
        self,
        pass_id: str,
        request: CrossrefPageRequest,
        created_at: datetime,
    ) -> CrossrefJournalPage:
        self._text(pass_id, "invalid_crossref_pass_id", 256)
        if not isinstance(request, CrossrefPageRequest):
            raise CrossrefHarvestJournalError("invalid_crossref_page_request")
        self._fingerprint(
            request.query_fingerprint,
            "invalid_crossref_page_request",
        )
        self._fingerprint(
            request.parameters_fingerprint,
            "invalid_crossref_page_request",
        )
        self._fingerprint(
            request.request_fingerprint,
            "invalid_crossref_page_request",
        )
        cursor = self._text(
            request.cursor,
            "invalid_crossref_page_request",
            65536,
        )
        created = self._time(created_at)
        with self._transaction(write=True) as connection:
            state = connection.execute(
                "SELECT p.*,w.query_fingerprint FROM crossref_harvest_passes p "
                "JOIN crossref_harvest_windows w ON w.id=p.window_id WHERE p.id=?",
                (pass_id,),
            ).fetchone()
            if state is None:
                raise CrossrefHarvestJournalError("crossref_pass_missing")
            if state["state"] != "running":
                raise CrossrefHarvestJournalError("crossref_pass_not_running")
            if (
                state["query_fingerprint"] != request.query_fingerprint
                or state["parameters_fingerprint"]
                != request.parameters_fingerprint
                or state["current_cursor"] != cursor
            ):
                raise CrossrefHarvestJournalError("crossref_page_identity_mismatch")
            page_no = state["next_page_no"]
            page_id = self._page_id(
                pass_id,
                page_no,
                request.request_fingerprint,
            )
            existing = connection.execute(
                "SELECT * FROM crossref_harvest_pages "
                "WHERE pass_id=? AND page_no=?",
                (pass_id, page_no),
            ).fetchone()
            if existing is not None:
                if (
                    existing["id"] != page_id
                    or existing["request_fingerprint"]
                    != request.request_fingerprint
                    or existing["cursor_in"] != cursor
                ):
                    raise CrossrefHarvestJournalError("crossref_page_conflict")
                return self._page_state(connection, existing)
            connection.execute(
                "INSERT INTO crossref_harvest_pages("
                "id,pass_id,page_no,cursor_in,request_fingerprint,state,created_at"
                ") VALUES(?,?,?,?,?,'requested',?)",
                (
                    page_id,
                    pass_id,
                    page_no,
                    cursor,
                    request.request_fingerprint,
                    created,
                ),
            )
            row = connection.execute(
                "SELECT * FROM crossref_harvest_pages WHERE id=?",
                (page_id,),
            ).fetchone()
            assert row is not None
            return self._page_state(connection, row)

    @classmethod
    def _require_receipt(
        cls,
        connection: sqlite3.Connection,
        receipt_id: str,
    ) -> None:
        value = cls._text(
            receipt_id,
            "crossref_receipt_object_mismatch",
            256,
        )
        row = connection.execute(
            "SELECT kind,state,content_sha256 FROM object_registry WHERE object_id=?",
            (value,),
        ).fetchone()
        if (
            row is None
            or row["kind"] != "raw"
            or row["state"] != "available"
            or value != "raw:" + row["content_sha256"]
        ):
            raise CrossrefHarvestJournalError(
                "crossref_receipt_object_mismatch"
            )

    def record_attempt(
        self,
        page_id: str,
        receipt_id: str,
        *,
        action: str,
        failure_code: str | None,
        recorded_at: datetime,
    ) -> CrossrefJournalPage:
        self._text(page_id, "invalid_crossref_page_id", 256)
        if action not in {"accept", "retry", "stop"}:
            raise CrossrefHarvestJournalError("invalid_crossref_attempt")
        if action == "accept":
            if failure_code is not None:
                raise CrossrefHarvestJournalError("invalid_crossref_attempt")
            failure = None
        else:
            failure = self._text(
                failure_code,
                "invalid_crossref_attempt",
                128,
            )
        recorded = self._time(recorded_at)
        with self._transaction(write=True) as connection:
            page = connection.execute(
                "SELECT * FROM crossref_harvest_pages WHERE id=?",
                (page_id,),
            ).fetchone()
            if page is None:
                raise CrossrefHarvestJournalError("crossref_page_missing")
            self._require_receipt(connection, receipt_id)
            prior = connection.execute(
                "SELECT * FROM crossref_harvest_page_attempts WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if prior is not None:
                if (
                    prior["page_id"] != page_id
                    or prior["action"] != action
                    or prior["failure_code"] != failure
                ):
                    raise CrossrefHarvestJournalError("crossref_attempt_conflict")
                return self._page_state(connection, page)
            if page["state"] in {"decoded", "accounted", "failed"}:
                raise CrossrefHarvestJournalError("crossref_attempt_conflict")
            if action == "accept" and page["successful_receipt_id"] is not None:
                if page["successful_receipt_id"] != receipt_id:
                    raise CrossrefHarvestJournalError("crossref_attempt_conflict")
                return self._page_state(connection, page)
            attempt_no = connection.execute(
                "SELECT COALESCE(MAX(attempt_no),0)+1 "
                "FROM crossref_harvest_page_attempts WHERE page_id=?",
                (page_id,),
            ).fetchone()[0]
            attempt_id = "crossref-attempt:" + self._hash(
                page_id,
                attempt_no,
                receipt_id,
            )
            connection.execute(
                "INSERT INTO crossref_harvest_page_attempts("
                "id,page_id,attempt_no,receipt_id,action,failure_code,recorded_at"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    page_id,
                    attempt_no,
                    receipt_id,
                    action,
                    failure,
                    recorded,
                ),
            )
            if action == "accept":
                connection.execute(
                    "UPDATE crossref_harvest_pages SET state='captured',"
                    "successful_receipt_id=?,last_error_code=NULL WHERE id=?",
                    (receipt_id, page_id),
                )
            else:
                connection.execute(
                    "UPDATE crossref_harvest_pages SET last_error_code=? "
                    "WHERE id=?",
                    (failure, page_id),
                )
            updated = connection.execute(
                "SELECT * FROM crossref_harvest_pages WHERE id=?",
                (page_id,),
            ).fetchone()
            assert updated is not None
            return self._page_state(connection, updated)

    def resume_page(self, pass_id: str) -> CrossrefJournalPage | None:
        self._text(pass_id, "invalid_crossref_pass_id", 256)
        with self._transaction(write=False) as connection:
            state = connection.execute(
                "SELECT * FROM crossref_harvest_passes WHERE id=?",
                (pass_id,),
            ).fetchone()
            if state is None:
                raise CrossrefHarvestJournalError("crossref_pass_missing")
            row = connection.execute(
                "SELECT * FROM crossref_harvest_pages "
                "WHERE pass_id=? AND page_no=?",
                (pass_id, state["next_page_no"]),
            ).fetchone()
            return None if row is None else self._page_state(connection, row)


    def note_page_error(
        self,
        page_id: str,
        error_code: str,
    ) -> CrossrefJournalPage:
        self._text(page_id, "invalid_crossref_page_id", 256)
        error = self._text(error_code, "invalid_crossref_page_error", 128)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM crossref_harvest_pages WHERE id=?",
                (page_id,),
            ).fetchone()
            if row is None:
                raise CrossrefHarvestJournalError("crossref_page_missing")
            if row["state"] == "accounted":
                raise CrossrefHarvestJournalError("crossref_page_conflict")
            connection.execute(
                "UPDATE crossref_harvest_pages SET last_error_code=? WHERE id=?",
                (error, page_id),
            )
            updated = connection.execute(
                "SELECT * FROM crossref_harvest_pages WHERE id=?",
                (page_id,),
            ).fetchone()
            assert updated is not None
            return self._page_state(connection, updated)

    @staticmethod
    def _validate_item(
        item: object,
        expected_ordinal: int,
    ) -> tuple[str | None, str, str, str, str | None]:
        from libs.discovery.dtos.crossref_page import CrossrefDecodedItem

        if not isinstance(item, CrossrefDecodedItem) or item.ordinal != expected_ordinal:
            raise CrossrefHarvestJournalError("invalid_crossref_decoded_page")
        try:
            encoded = item.canonical_json.encode("ascii")
            parsed = json.loads(item.canonical_json)
            canonical = json.dumps(
                parsed,
                sort_keys=True,
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (
            UnicodeEncodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            raise CrossrefHarvestJournalError(
                "invalid_crossref_decoded_page"
            ) from None
        if (
            canonical != item.canonical_json
            or hashlib.sha256(encoded).hexdigest()
            != item.canonical_sha256
            or item.state not in {"decoded", "quarantined"}
            or (item.state == "decoded" and item.error_code is not None)
            or (
                item.state == "quarantined"
                and (
                    not isinstance(item.error_code, str)
                    or not item.error_code
                )
            )
        ):
            raise CrossrefHarvestJournalError("invalid_crossref_decoded_page")
        raw_doi = item.doi_raw
        if raw_doi is not None:
            SqliteCrossrefHarvestJournalAdapter._text(
                raw_doi,
                "invalid_crossref_decoded_page",
                2048,
            )
        outcome = "pending" if item.state == "decoded" else "quarantined"
        return (
            raw_doi,
            item.canonical_json,
            item.canonical_sha256,
            outcome,
            item.error_code,
        )

    def save_decoded(
        self,
        page_id: str,
        replayed: CrossrefReplayedPage,
        decoded_at: datetime,
    ) -> CrossrefJournalPage:
        self._text(page_id, "invalid_crossref_page_id", 256)
        if not isinstance(replayed, CrossrefReplayedPage):
            raise CrossrefHarvestJournalError("invalid_crossref_decoded_page")
        decoded = self._time(decoded_at)
        with self._transaction(write=True) as connection:
            page = connection.execute(
                "SELECT p.*,x.parameters_fingerprint,w.query_fingerprint "
                "FROM crossref_harvest_pages p "
                "JOIN crossref_harvest_passes x ON x.id=p.pass_id "
                "JOIN crossref_harvest_windows w ON w.id=x.window_id "
                "WHERE p.id=?",
                (page_id,),
            ).fetchone()
            if page is None:
                raise CrossrefHarvestJournalError("crossref_page_missing")
            if page["state"] in {"decoded", "accounted"}:
                if (
                    page["successful_receipt_id"] == replayed.receipt_id
                    and page["entity_sha256"] == replayed.entity_sha256
                    and page["content_coded_sha256"]
                    == replayed.content_coded_sha256
                ):
                    return self._page_state(connection, page)
                raise CrossrefHarvestJournalError("crossref_decoded_page_conflict")
            if (
                page["state"] != "captured"
                or page["successful_receipt_id"] != replayed.receipt_id
                or replayed.page.query_fingerprint
                != page["query_fingerprint"]
                or replayed.page.parameters_fingerprint
                != page["parameters_fingerprint"]
                or replayed.page.request_fingerprint
                != page["request_fingerprint"]
                or replayed.page.cursor_in != page["cursor_in"]
                or replayed.page.response_sha256
                != replayed.entity_sha256
            ):
                raise CrossrefHarvestJournalError("crossref_decoded_page_conflict")
            self._fingerprint(
                replayed.content_coded_sha256,
                "invalid_crossref_decoded_page",
            )
            self._fingerprint(
                replayed.entity_sha256,
                "invalid_crossref_decoded_page",
            )
            items = replayed.page.items
            if not isinstance(items, tuple):
                raise CrossrefHarvestJournalError("invalid_crossref_decoded_page")
            prepared = [
                self._validate_item(item, ordinal)
                for ordinal, item in enumerate(items)
            ]
            for ordinal, values in enumerate(prepared):
                raw_doi, canonical_json, canonical_sha, outcome, error = values
                connection.execute(
                    "INSERT INTO crossref_harvest_items("
                    "page_id,ordinal,raw_doi,canonical_json,canonical_sha256,"
                    "decode_state,outcome_state,error_code,created_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        page_id,
                        ordinal,
                        raw_doi,
                        canonical_json,
                        canonical_sha,
                        items[ordinal].state,
                        outcome,
                        error,
                        decoded,
                    ),
                )
            connection.execute(
                "UPDATE crossref_harvest_pages SET state='decoded',"
                "cursor_out=?,content_coded_sha256=?,entity_sha256=?,"
                "parser_version=?,reported_total=?,item_count=?,"
                "traversal_end_hint=?,decoded_at=?,last_error_code=NULL "
                "WHERE id=? AND state='captured'",
                (
                    replayed.page.next_cursor,
                    replayed.content_coded_sha256,
                    replayed.entity_sha256,
                    replayed.page.parser_version,
                    replayed.page.reported_total,
                    len(items),
                    1 if replayed.page.traversal_end_hint else 0,
                    decoded,
                    page_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM crossref_harvest_pages WHERE id=?",
                (page_id,),
            ).fetchone()
            assert updated is not None
            return self._page_state(connection, updated)

    @staticmethod
    def _has_projection_quarantine(connection: sqlite3.Connection) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='crossref_projection_quarantines'"
        ).fetchone() is not None

    def pending_items(
        self,
        page_id: str,
    ) -> tuple[CrossrefPendingItem, ...]:
        self._text(page_id, "invalid_crossref_page_id", 256)
        with self._transaction(write=False) as connection:
            page = connection.execute(
                "SELECT state FROM crossref_harvest_pages WHERE id=?",
                (page_id,),
            ).fetchone()
            if page is None:
                raise CrossrefHarvestJournalError("crossref_page_missing")
            if self._has_projection_quarantine(connection):
                rows = connection.execute(
                    "SELECT page_id,ordinal,raw_doi,canonical_json,canonical_sha256 "
                    "FROM crossref_harvest_items i "
                    "WHERE i.page_id=? AND i.outcome_state='pending' "
                    "AND NOT EXISTS("
                    "SELECT 1 FROM crossref_projection_quarantines q "
                    "WHERE q.page_id=i.page_id AND q.ordinal=i.ordinal"
                    ") ORDER BY i.ordinal",
                    (page_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT page_id,ordinal,raw_doi,canonical_json,canonical_sha256 "
                    "FROM crossref_harvest_items "
                    "WHERE page_id=? AND outcome_state='pending' ORDER BY ordinal",
                    (page_id,),
                ).fetchall()
            return tuple(CrossrefPendingItem(*tuple(row)) for row in rows)

    def mark_processed(
        self,
        page_id: str,
        ordinal: int,
        *,
        canonical_doi: str,
        outcome_ref: str,
        processed_at: datetime,
    ) -> bool:
        self._text(page_id, "invalid_crossref_page_id", 256)
        if type(ordinal) is not int or ordinal < 0:
            raise CrossrefHarvestJournalError("invalid_crossref_item")
        doi = self._text(
            canonical_doi,
            "invalid_crossref_item",
            2048,
        )
        reference = self._text(
            outcome_ref,
            "invalid_crossref_item",
            512,
        )
        processed = self._time(processed_at)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM crossref_harvest_items "
                "WHERE page_id=? AND ordinal=?",
                (page_id, ordinal),
            ).fetchone()
            if row is None:
                raise CrossrefHarvestJournalError("crossref_item_missing")
            if row["decode_state"] != "decoded":
                raise CrossrefHarvestJournalError("crossref_item_not_processable")
            if row["outcome_state"] == "processed":
                if (
                    row["canonical_doi"] == doi
                    and row["outcome_ref"] == reference
                ):
                    return True
                raise CrossrefHarvestJournalError(
                    "crossref_item_outcome_conflict"
                )
            if row["outcome_state"] != "pending":
                raise CrossrefHarvestJournalError(
                    "crossref_item_outcome_conflict"
                )
            if self._has_projection_quarantine(connection):
                quarantine = connection.execute(
                    "SELECT 1 FROM crossref_projection_quarantines "
                    "WHERE page_id=? AND ordinal=?",
                    (page_id, ordinal),
                ).fetchone()
                if quarantine is not None:
                    raise CrossrefHarvestJournalError(
                        "crossref_item_outcome_conflict"
                    )
            connection.execute(
                "UPDATE crossref_harvest_items SET outcome_state='processed',"
                "canonical_doi=?,outcome_ref=?,processed_at=? "
                "WHERE page_id=? AND ordinal=? AND outcome_state='pending'",
                (doi, reference, processed, page_id, ordinal),
            )
            return False

    def mark_projection_quarantined(
        self,
        page_id: str,
        ordinal: int,
        *,
        error_code: str,
        quarantined_at: datetime,
    ) -> bool:
        self._text(page_id, "invalid_crossref_page_id", 256)
        if type(ordinal) is not int or ordinal < 0:
            raise CrossrefHarvestJournalError("invalid_crossref_item")
        error = self._text(
            error_code,
            "invalid_crossref_projection_quarantine",
            128,
        )
        quarantined = self._time(quarantined_at)
        with self._transaction(write=True) as connection:
            if not self._has_projection_quarantine(connection):
                raise CrossrefHarvestJournalError(
                    "crossref_projection_quarantine_unavailable"
                )
            item = connection.execute(
                "SELECT * FROM crossref_harvest_items "
                "WHERE page_id=? AND ordinal=?",
                (page_id, ordinal),
            ).fetchone()
            if item is None:
                raise CrossrefHarvestJournalError("crossref_item_missing")
            if (
                item["decode_state"] != "decoded"
                or item["outcome_state"] != "pending"
            ):
                raise CrossrefHarvestJournalError(
                    "crossref_item_outcome_conflict"
                )
            existing = connection.execute(
                "SELECT * FROM crossref_projection_quarantines "
                "WHERE page_id=? AND ordinal=?",
                (page_id, ordinal),
            ).fetchone()
            if existing is not None:
                if (
                    existing["error_code"] == error
                    and existing["canonical_sha256"]
                    == item["canonical_sha256"]
                ):
                    return True
                raise CrossrefHarvestJournalError(
                    "crossref_item_outcome_conflict"
                )
            connection.execute(
                "INSERT INTO crossref_projection_quarantines "
                "VALUES(?,?,?,?,?)",
                (
                    page_id,
                    ordinal,
                    error,
                    item["canonical_sha256"],
                    quarantined,
                ),
            )
            return False

    @classmethod
    def _aggregate(
        cls,
        connection: sqlite3.Connection,
        pass_id: str,
    ) -> tuple[int, int, int, int, int, bool, int | None]:
        if cls._has_projection_quarantine(connection):
            quarantine_expression = (
                "SUM(CASE WHEN i.outcome_state='quarantined' "
                "OR EXISTS(SELECT 1 FROM crossref_projection_quarantines q "
                "WHERE q.page_id=i.page_id AND q.ordinal=i.ordinal) "
                "THEN 1 ELSE 0 END),"
            )
        else:
            quarantine_expression = (
                "SUM(CASE WHEN i.outcome_state='quarantined' THEN 1 ELSE 0 END),"
            )
        row = connection.execute(
            "SELECT count(i.ordinal),"
            "SUM(CASE WHEN i.outcome_state='processed' THEN 1 ELSE 0 END),"
            + quarantine_expression
            + "COUNT(DISTINCT CASE WHEN i.outcome_state='processed' "
            "THEN i.canonical_doi END) "
            "FROM crossref_harvest_items i "
            "JOIN crossref_harvest_pages p ON p.id=i.page_id "
            "WHERE p.pass_id=? AND p.state='accounted'",
            (pass_id,),
        ).fetchone()
        raw = int(row[0] or 0)
        processed = int(row[1] or 0)
        quarantine = int(row[2] or 0)
        unique = int(row[3] or 0)
        duplicate = max(0, processed - unique)
        totals = [
            value[0]
            for value in connection.execute(
                "SELECT reported_total FROM crossref_harvest_pages "
                "WHERE pass_id=? AND state='accounted' "
                "AND reported_total IS NOT NULL ORDER BY page_no",
                (pass_id,),
            ).fetchall()
        ]
        first = totals[0] if totals else None
        changed = len(set(totals)) > 1
        return raw, processed, quarantine, unique, duplicate, changed, first

    def commit_page(
        self,
        pass_id: str,
        page_id: str,
        committed_at: datetime,
    ) -> CrossrefHarvestPassState:
        self._text(pass_id, "invalid_crossref_pass_id", 256)
        self._text(page_id, "invalid_crossref_page_id", 256)
        committed = self._time(committed_at)
        with self._transaction(write=True) as connection:
            state = connection.execute(
                "SELECT * FROM crossref_harvest_passes WHERE id=?",
                (pass_id,),
            ).fetchone()
            page = connection.execute(
                "SELECT * FROM crossref_harvest_pages WHERE id=?",
                (page_id,),
            ).fetchone()
            if state is None:
                raise CrossrefHarvestJournalError("crossref_pass_missing")
            if page is None or page["pass_id"] != pass_id:
                raise CrossrefHarvestJournalError("crossref_page_missing")
            if page["state"] == "accounted":
                return self._pass_state(state)
            if (
                state["state"] != "running"
                or page["state"] != "decoded"
                or page["page_no"] != state["next_page_no"]
                or page["cursor_in"] != state["current_cursor"]
            ):
                raise CrossrefHarvestJournalError("crossref_checkpoint_conflict")
            if self._has_projection_quarantine(connection):
                pending = connection.execute(
                    "SELECT count(*) FROM crossref_harvest_items i "
                    "WHERE i.page_id=? AND i.outcome_state='pending' "
                    "AND NOT EXISTS("
                    "SELECT 1 FROM crossref_projection_quarantines q "
                    "WHERE q.page_id=i.page_id AND q.ordinal=i.ordinal"
                    ")",
                    (page_id,),
                ).fetchone()[0]
            else:
                pending = connection.execute(
                    "SELECT count(*) FROM crossref_harvest_items "
                    "WHERE page_id=? AND outcome_state='pending'",
                    (page_id,),
                ).fetchone()[0]
            if pending:
                raise CrossrefHarvestJournalError(
                    "crossref_page_not_accounted"
                )
            end_hint = bool(page["traversal_end_hint"])
            cursor_out = page["cursor_out"]
            if not end_hint:
                if not isinstance(cursor_out, str) or not cursor_out:
                    raise CrossrefHarvestJournalError(
                        "crossref_next_cursor_missing"
                    )
                cycle = connection.execute(
                    "SELECT 1 FROM crossref_harvest_pages "
                    "WHERE pass_id=? AND page_no<? AND state='accounted' "
                    "AND (cursor_in=? OR cursor_out=?) LIMIT 1",
                    (
                        pass_id,
                        page["page_no"],
                        cursor_out,
                        cursor_out,
                    ),
                ).fetchone()
                if cycle is not None:
                    raise CrossrefHarvestJournalError(
                        "crossref_cursor_cycle"
                    )
            changed = connection.execute(
                "UPDATE crossref_harvest_pages SET state='accounted',"
                "committed_at=? WHERE id=? AND state='decoded'",
                (committed, page_id),
            ).rowcount
            if changed != 1:
                raise CrossrefHarvestJournalError("crossref_checkpoint_conflict")

            (
                raw,
                processed,
                quarantine,
                unique,
                duplicate,
                total_changed,
                first_total,
            ) = self._aggregate(connection, pass_id)
            drift = total_changed or (
                end_hint
                and first_total is not None
                and raw != first_total
            )
            repair = end_hint and (drift or quarantine > 0)
            if end_hint:
                pass_state = "completed"
                next_cursor = None
                traversal = accounting = 1
                completeness = "unknown" if repair else "provisional"
                finished = committed
                window_state = "repair_pending" if repair else "traversed"
            else:
                pass_state = "running"
                next_cursor = cursor_out
                traversal = accounting = 0
                completeness = "unknown"
                finished = None
                window_state = "running"
            changed = connection.execute(
                "UPDATE crossref_harvest_passes SET state=?,current_cursor=?,"
                "next_page_no=next_page_no+1,traversal_complete=?,"
                "accounting_complete=?,first_reported_total=?,"
                "raw_item_count=?,processed_item_count=?,quarantine_count=?,"
                "unique_doi_count=?,duplicate_doi_count=?,parse_gap_count=?,"
                "drift_suspected=?,repair_pending=?,source_completeness=?,"
                "finished_at=? WHERE id=? AND state='running' "
                "AND next_page_no=? AND current_cursor=?",
                (
                    pass_state,
                    next_cursor,
                    traversal,
                    accounting,
                    first_total,
                    raw,
                    processed,
                    quarantine,
                    unique,
                    duplicate,
                    quarantine,
                    1 if drift else 0,
                    1 if repair else 0,
                    completeness,
                    finished,
                    pass_id,
                    page["page_no"],
                    page["cursor_in"],
                ),
            ).rowcount
            if changed != 1:
                raise CrossrefHarvestJournalError("crossref_checkpoint_conflict")
            connection.execute(
                "UPDATE crossref_harvest_windows SET state=?,updated_at=? "
                "WHERE id=?",
                (window_state, committed, state["window_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM crossref_harvest_passes WHERE id=?",
                (pass_id,),
            ).fetchone()
            assert updated is not None
            return self._pass_state(updated)
