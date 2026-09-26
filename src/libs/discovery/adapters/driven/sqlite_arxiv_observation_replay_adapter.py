import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import (
    PARSER_VERSION,
    ArxivAtomParserAdapter,
)
from libs.discovery.domain.services.harvest_page_rules import HarvestPageRules
from libs.discovery.dtos.arxiv_observation_replay import ArxivObservationReplay
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.arxiv_observation_replay_error import (
    ArxivObservationReplayError as Error,
)
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.exceptions.source_parse_error import SourceParseError
from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.read_object_port import ReadObjectPort


@dataclass(frozen=True)
class _ReplaySnapshot:
    observation_id: str
    unit_id: str
    native_id: str
    payload_object_id: str
    parser_version: str
    observed_at: datetime
    request: SourcePageRequest
    response_sha256: str
    byte_size: int


class SqliteArxivObservationReplayAdapter:
    """Replay one durable arXiv observation without provider I/O."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        read_object: ReadObjectPort,
    ) -> None:
        self._connect = connect
        self._read_object = read_object
        self._parser = ArxivAtomParserAdapter()

    @staticmethod
    def _observation_id(value: object) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"observation:[0-9a-f]{64}", value) is None
        ):
            raise Error("invalid_arxiv_observation_id")
        return value

    @staticmethod
    def _instant(value: object) -> datetime:
        if not isinstance(value, str):
            raise Error("arxiv_observation_state_corrupt")
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise Error("arxiv_observation_state_corrupt") from None
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise Error("arxiv_observation_state_corrupt")
        return moment.astimezone(timezone.utc)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise Error("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise Error("foreign_keys_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except Error:
            raise
        except sqlite3.Error as exc:
            raise Error("arxiv_observation_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _request(value: object) -> SourcePageRequest:
        if not isinstance(value, dict):
            raise Error("arxiv_observation_state_corrupt")
        try:
            request = SourcePageRequest(**value)
        except TypeError:
            raise Error("arxiv_observation_state_corrupt") from None
        if request.source_id != "arxiv":
            raise Error("arxiv_observation_state_corrupt")
        return request

    @classmethod
    def _capture_candidate(
        cls,
        row: sqlite3.Row,
        payload_object_id: str,
        observed_at: str,
    ) -> tuple[SourcePageRequest, str, int] | None:
        try:
            envelope = HarvestPageRules.decode(row["response_metadata_json"])
        except HarvestError as exc:
            raise Error("arxiv_observation_state_corrupt") from exc
        capture = envelope.get("capture")
        if not isinstance(capture, dict):
            return None
        if capture.get("raw_object_id") != payload_object_id:
            return None
        request = cls._request(envelope.get("request"))
        sha = capture.get("response_sha256")
        size = capture.get("byte_size")
        if (
            row["state"] != "captured"
            or capture.get("status") != 200
            or capture.get("failure_code") is not None
            or capture.get("body_complete") is not True
            or capture.get("capture_error") is not None
            or capture.get("request_fingerprint") != request.request_fingerprint
            or capture.get("received_at") != observed_at
            or not isinstance(sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha) is None
            or payload_object_id != "raw:" + sha
            or type(size) is not int
            or size < 0
        ):
            raise Error("arxiv_observation_state_corrupt")
        return request, sha, size

    def _snapshot(self, observation_id: str) -> _ReplaySnapshot:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT o.*,u.state AS unit_state,b.source AS binding_source,"
                "b.query_fingerprint AS binding_query_fingerprint "
                "FROM source_observations o "
                "JOIN harvest_units u ON u.id=o.unit_id "
                "JOIN source_bindings b ON b.id=u.binding_id WHERE o.id=?",
                (observation_id,),
            ).fetchone()
            if row is None:
                raise Error("arxiv_observation_missing")
            if (
                row["source"] != "arxiv"
                or row["binding_source"] != "arxiv"
                or row["unit_state"] not in {"partial", "succeeded"}
                or not isinstance(row["native_id"], str)
                or not row["native_id"]
                or not isinstance(row["payload_object_id"], str)
                or re.fullmatch(
                    r"raw:[0-9a-f]{64}",
                    row["payload_object_id"],
                )
                is None
            ):
                raise Error("arxiv_observation_state_corrupt")
            if row["parser_version"] != PARSER_VERSION:
                raise Error("arxiv_observation_parser_version_mismatch")

            candidates = []
            attempts = connection.execute(
                "SELECT state,response_metadata_json FROM harvest_attempts "
                "WHERE unit_id=? AND response_metadata_json IS NOT NULL "
                "ORDER BY attempt_no",
                (row["unit_id"],),
            ).fetchall()
            for attempt in attempts:
                candidate = self._capture_candidate(
                    attempt,
                    row["payload_object_id"],
                    row["observed_at"],
                )
                if candidate is not None:
                    candidates.append(candidate)
            if not candidates:
                raise Error("arxiv_observation_capture_missing")
            first = candidates[0]
            if any(candidate != first for candidate in candidates[1:]):
                raise Error("arxiv_observation_capture_ambiguous")
            request, sha, size = first
            if request.query_fingerprint != row["binding_query_fingerprint"]:
                raise Error("arxiv_observation_state_corrupt")
            return _ReplaySnapshot(
                row["id"],
                row["unit_id"],
                row["native_id"],
                row["payload_object_id"],
                row["parser_version"],
                self._instant(row["observed_at"]),
                request,
                sha,
                size,
            )

    def __call__(self, observation_id: str) -> ArxivObservationReplay:
        identity = self._observation_id(observation_id)
        before = self._snapshot(identity)
        try:
            body = self._read_object(before.payload_object_id)
        except StorageError as exc:
            raise Error("arxiv_observation_raw_unavailable") from exc
        if (
            not isinstance(body, bytes)
            or len(body) != before.byte_size
            or hashlib.sha256(body).hexdigest() != before.response_sha256
        ):
            raise Error("arxiv_observation_raw_corrupt")
        try:
            page = self._parser(before.request, body, http_status=200)
        except SourceParseError as exc:
            raise Error("arxiv_observation_parse_failed") from exc
        if (
            page.parser_version != before.parser_version
            or page.request_fingerprint != before.request.request_fingerprint
            or page.response_sha256 != before.response_sha256
        ):
            raise Error("arxiv_observation_parse_mismatch")
        records = tuple(
            record
            for record in page.records
            if record.source_record_id == before.native_id
        )
        if len(records) != 1:
            raise Error("arxiv_observation_record_mismatch")
        after = self._snapshot(identity)
        if after != before:
            raise Error("arxiv_observation_state_changed")
        return ArxivObservationReplay(
            before.observation_id,
            before.unit_id,
            before.payload_object_id,
            before.parser_version,
            before.observed_at,
            records[0],
        )
