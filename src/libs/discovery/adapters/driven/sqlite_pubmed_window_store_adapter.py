import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.pubmed_search_page import PubmedSearchPage
from libs.discovery.dtos.pubmed_window import (
    PreparedPubmedBibliographyObservation,
    PubmedPendingSearchPage,
    PubmedWindowProgress,
)
from libs.discovery.exceptions.pubmed_window_error import PubmedWindowError


class SqlitePubmedWindowStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _time(value: object, code: str) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, OverflowError):
                raise PubmedWindowError(code) from None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PubmedWindowError(code)
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise PubmedWindowError(code) from None

    @classmethod
    def _time_text(cls, value: object, code: str) -> str:
        return cls._time(value, code).isoformat()

    @staticmethod
    def _text(value: object, code: str, *, maximum: int = 512) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > maximum
            or "\0" in value
        ):
            raise PubmedWindowError(code)
        return value

    @staticmethod
    def _fingerprint(value: object, code: str) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise PubmedWindowError(code)
        return value

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.in_transaction:
                raise PubmedWindowError("owned_connection_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except PubmedWindowError:
            raise
        except sqlite3.IntegrityError as exc:
            raise PubmedWindowError("pubmed_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "pubmed_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "pubmed_database_error"
            )
            raise PubmedWindowError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _plan(
        cls,
        plan: CompiledSourceQuery,
    ) -> tuple[str, datetime, datetime]:
        if (
            not isinstance(plan, CompiledSourceQuery)
            or plan.source_id != "pubmed"
            or not isinstance(plan.provenance_json, str)
        ):
            raise PubmedWindowError("invalid_pubmed_plan")
        cls._fingerprint(plan.query_fingerprint, "invalid_pubmed_plan")
        try:
            data = json.loads(plan.provenance_json)
            inner = data["input"]
            start = cls._time(inner["window_start"], "invalid_pubmed_plan")
            end = cls._time(inner["window_end"], "invalid_pubmed_plan")
        except (json.JSONDecodeError, KeyError, TypeError):
            raise PubmedWindowError("invalid_pubmed_plan") from None
        if start >= end:
            raise PubmedWindowError("invalid_pubmed_plan")
        return plan.query_fingerprint, start, end

    @classmethod
    def _progress(
        cls,
        connection: sqlite3.Connection,
        business_key: str,
    ) -> PubmedWindowProgress:
        row = connection.execute(
            "SELECT * FROM pubmed_window_progress WHERE business_key=?",
            (business_key,),
        ).fetchone()
        if row is None:
            raise PubmedWindowError("pubmed_window_missing")
        if row["state"] not in {"pending", "succeeded", "verified_empty"}:
            raise PubmedWindowError("pubmed_window_corrupt")
        next_start = row["next_start"]
        total = row["total_results"]
        if (
            type(next_start) is not int
            or next_start < 0
            or (total is not None and (type(total) is not int or total < next_start))
        ):
            raise PubmedWindowError("pubmed_window_corrupt")

        pending = None
        search = connection.execute(
            "SELECT * FROM pubmed_search_pages "
            "WHERE business_key=? AND start_index=?",
            (business_key, next_start),
        ).fetchone()
        completed = connection.execute(
            "SELECT count(*) FROM pubmed_bibliography_observations "
            "WHERE business_key=? AND start_index=?",
            (business_key, next_start),
        ).fetchone()[0]
        if completed:
            raise PubmedWindowError("pubmed_window_corrupt")
        if search is not None:
            try:
                pmids = json.loads(search["pmids_json"])
            except json.JSONDecodeError:
                raise PubmedWindowError("pubmed_window_corrupt") from None
            if (
                not isinstance(pmids, list)
                or any(not isinstance(value, str) or not value for value in pmids)
                or len(pmids) != len(set(pmids))
            ):
                raise PubmedWindowError("pubmed_window_corrupt")
            pending = PubmedPendingSearchPage(
                search["start_index"],
                tuple(pmids),
                search["raw_object_id"],
                search["request_fingerprint"],
                search["response_sha256"],
                cls._time(search["observed_at"], "pubmed_window_corrupt"),
            )
        return PubmedWindowProgress(
            row["business_key"],
            row["query_fingerprint"],
            cls._time(row["window_start"], "pubmed_window_corrupt"),
            cls._time(row["window_end"], "pubmed_window_corrupt"),
            next_start,
            total,
            row["state"],
            pending,
        )

    def ensure(
        self,
        business_key: str,
        plan: CompiledSourceQuery,
        *,
        created_at: datetime,
    ) -> PubmedWindowProgress:
        business_key = self._text(
            business_key,
            "invalid_pubmed_business_key",
            maximum=768,
        )
        query_fingerprint, start, end = self._plan(plan)
        created = self._time_text(created_at, "invalid_pubmed_time")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM pubmed_window_progress WHERE business_key=?",
                (business_key,),
            ).fetchone()
            expected = (
                query_fingerprint,
                start.isoformat(),
                end.isoformat(),
            )
            if row is None:
                connection.execute(
                    "INSERT INTO pubmed_window_progress("
                    "business_key,query_fingerprint,window_start,window_end,"
                    "next_start,total_results,state,created_at,updated_at"
                    ") VALUES(?,?,?,?,0,NULL,'pending',?,?)",
                    (
                        business_key,
                        *expected,
                        created,
                        created,
                    ),
                )
            elif (
                row["query_fingerprint"],
                row["window_start"],
                row["window_end"],
            ) != expected:
                raise PubmedWindowError("pubmed_window_identity_conflict")
            return self._progress(connection, business_key)

    def read(self, business_key: str) -> PubmedWindowProgress:
        business_key = self._text(
            business_key,
            "invalid_pubmed_business_key",
            maximum=768,
        )
        with self._transaction(write=False) as connection:
            return self._progress(connection, business_key)

    def record_search_page(
        self,
        business_key: str,
        page: PubmedSearchPage,
        *,
        raw_object_id: str,
        observed_at: datetime,
    ) -> PubmedWindowProgress:
        business_key = self._text(
            business_key,
            "invalid_pubmed_business_key",
            maximum=768,
        )
        if not isinstance(page, PubmedSearchPage):
            raise PubmedWindowError("invalid_pubmed_search_page")
        if raw_object_id != "raw:" + page.response_sha256:
            raise PubmedWindowError("pubmed_raw_object_mismatch")
        observed = self._time_text(observed_at, "invalid_pubmed_time")
        pmids_json = json.dumps(
            list(page.pmids),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        page_id = "pubmed-search:" + hashlib.sha256(
            (
                business_key
                + "\0"
                + str(page.observation.start_index)
                + "\0"
                + page.response_sha256
            ).encode("utf-8")
        ).hexdigest()
        with self._transaction(write=True) as connection:
            current = self._progress(connection, business_key)
            if current.state != "pending":
                return current
            if (
                page.observation.query_fingerprint != current.query_fingerprint
                or page.observation.start_index != current.next_start
                or tuple(page.observation.record_ids) != page.pmids
            ):
                raise PubmedWindowError("pubmed_search_page_conflict")
            if (
                current.total_results is not None
                and current.total_results != page.observation.total_results
            ):
                raise PubmedWindowError("pubmed_result_set_changed")
            existing = connection.execute(
                "SELECT * FROM pubmed_search_pages "
                "WHERE business_key=? AND start_index=?",
                (business_key, current.next_start),
            ).fetchone()
            expected = (
                page_id,
                pmids_json,
                raw_object_id,
                page.request_fingerprint,
                page.response_sha256,
                observed,
            )
            if existing is None:
                connection.execute(
                    "INSERT INTO pubmed_search_pages("
                    "id,business_key,start_index,pmids_json,raw_object_id,"
                    "request_fingerprint,response_sha256,observed_at"
                    ") VALUES(?,?,?,?,?,?,?,?)",
                    (
                        page_id,
                        business_key,
                        current.next_start,
                        pmids_json,
                        raw_object_id,
                        page.request_fingerprint,
                        page.response_sha256,
                        observed,
                    ),
                )
            elif (
                existing["id"],
                existing["pmids_json"],
                existing["raw_object_id"],
                existing["request_fingerprint"],
                existing["response_sha256"],
                existing["observed_at"],
            ) != expected:
                raise PubmedWindowError("pubmed_search_page_conflict")

            state = "pending"
            if page.observation.total_results == 0:
                if current.next_start != 0 or page.pmids:
                    raise PubmedWindowError("pubmed_search_page_conflict")
                state = "verified_empty"
            connection.execute(
                "UPDATE pubmed_window_progress "
                "SET total_results=?,state=?,updated_at=? WHERE business_key=?",
                (
                    page.observation.total_results,
                    state,
                    observed,
                    business_key,
                ),
            )
            return self._progress(connection, business_key)

    def commit_bibliography(
        self,
        business_key: str,
        observations: tuple[PreparedPubmedBibliographyObservation, ...],
    ) -> PubmedWindowProgress:
        business_key = self._text(
            business_key,
            "invalid_pubmed_business_key",
            maximum=768,
        )
        if (
            not isinstance(observations, tuple)
            or not observations
            or len(observations) > 200
        ):
            raise PubmedWindowError("invalid_pubmed_bibliography")
        with self._transaction(write=True) as connection:
            current = self._progress(connection, business_key)
            if current.state != "pending" or current.pending_page is None:
                raise PubmedWindowError("pubmed_bibliography_not_pending")
            pending = current.pending_page
            by_pmid = {item.pmid: item for item in observations}
            if (
                len(by_pmid) != len(observations)
                or set(by_pmid) != set(pending.pmids)
                or any(
                    not isinstance(item, PreparedPubmedBibliographyObservation)
                    or item.business_key != business_key
                    or item.start_index != current.next_start
                    for item in observations
                )
            ):
                raise PubmedWindowError("pubmed_bibliography_identity_mismatch")
            for item in observations:
                self._fingerprint(
                    item.content_fingerprint,
                    "invalid_pubmed_bibliography",
                )
                existing = connection.execute(
                    "SELECT * FROM pubmed_bibliography_observations WHERE id=?",
                    (item.observation_id,),
                ).fetchone()
                expected = (
                    business_key,
                    item.start_index,
                    item.pmid,
                    item.raw_object_id,
                    item.request_fingerprint,
                    item.response_sha256,
                    item.parser_version,
                    item.record_json,
                    item.content_fingerprint,
                    item.observed_at.isoformat(),
                )
                if existing is None:
                    connection.execute(
                        "INSERT INTO pubmed_bibliography_observations("
                        "id,business_key,start_index,pmid,raw_object_id,"
                        "request_fingerprint,response_sha256,parser_version,"
                        "record_json,content_fingerprint,observed_at"
                        ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (item.observation_id, *expected),
                    )
                elif tuple(existing)[1:] != expected:
                    raise PubmedWindowError("pubmed_bibliography_conflict")

            total = current.total_results
            if total is None:
                raise PubmedWindowError("pubmed_window_corrupt")
            next_start = current.next_start + len(pending.pmids)
            if next_start > total:
                raise PubmedWindowError("pubmed_window_corrupt")
            state = "succeeded" if next_start == total else "pending"
            updated = max(item.observed_at for item in observations).isoformat()
            connection.execute(
                "UPDATE pubmed_window_progress "
                "SET next_start=?,state=?,updated_at=? WHERE business_key=?",
                (next_start, state, updated, business_key),
            )
            return self._progress(connection, business_key)
