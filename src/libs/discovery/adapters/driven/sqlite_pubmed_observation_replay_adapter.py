import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.dtos.pubmed_observation_replay import PubmedObservationReplay
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.pubmed_observation_replay_error import (
    PubmedObservationReplayError as Error,
)
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
    pmids: tuple[str, ...]
    response_sha256: str
    byte_size: int


class SqlitePubmedObservationReplayAdapter:
    """Replay one durable PubMed bibliography observation without provider I/O."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        read_object: ReadObjectPort,
    ) -> None:
        self._connect = connect
        self._read_object = read_object
        self._parser = PubmedSourceAdapter(
            tool="paper-radar-replay",
            email="replay@example.invalid",
        )

    @staticmethod
    def _observation_id(value: object) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"observation:[0-9a-f]{64}", value) is None
        ):
            raise Error("invalid_pubmed_observation_id")
        return value

    @staticmethod
    def _instant(value: object) -> datetime:
        if not isinstance(value, str):
            raise Error("pubmed_observation_state_corrupt")
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise Error("pubmed_observation_state_corrupt") from None
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise Error("pubmed_observation_state_corrupt")
        return moment.astimezone(timezone.utc)

    @staticmethod
    def _canonical(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
            raise Error("pubmed_observation_state_corrupt") from None

    @classmethod
    def _pmids(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, str):
            raise Error("pubmed_observation_state_corrupt")
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, RecursionError):
            raise Error("pubmed_observation_state_corrupt") from None
        if (
            not isinstance(parsed, list)
            or not 1 <= len(parsed) <= 200
            or any(
                not isinstance(item, str)
                or re.fullmatch(r"[1-9][0-9]{0,9}", item) is None
                for item in parsed
            )
            or len(parsed) != len(set(parsed))
            or cls._canonical(parsed) != value
        ):
            raise Error("pubmed_observation_state_corrupt")
        return tuple(parsed)

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
            raise Error("pubmed_observation_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

    def _snapshot(self, observation_id: str) -> _ReplaySnapshot:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT o.*,u.state AS unit_state,b.source AS binding_source,"
                "b.enabled AS binding_enabled FROM source_observations o "
                "JOIN harvest_units u ON u.id=o.unit_id "
                "JOIN source_bindings b ON b.id=u.binding_id WHERE o.id=?",
                (observation_id,),
            ).fetchone()
            if row is None:
                raise Error("pubmed_observation_missing")
            if (
                row["source"] != "pubmed"
                or row["binding_source"] != "pubmed"
                or row["binding_enabled"] != 1
                or row["unit_state"] not in {"pending", "partial", "succeeded"}
                or row["parser_version"] != PubmedSourceAdapter.PARSER_VERSION
                or row["native_updated_at"] is not None
                or not isinstance(row["native_id"], str)
                or re.fullmatch(r"[1-9][0-9]{0,9}", row["native_id"]) is None
                or not isinstance(row["payload_object_id"], str)
                or re.fullmatch(
                    r"raw:[0-9a-f]{64}",
                    row["payload_object_id"],
                )
                is None
            ):
                if row["parser_version"] != PubmedSourceAdapter.PARSER_VERSION:
                    raise Error("pubmed_observation_parser_version_mismatch")
                raise Error("pubmed_observation_state_corrupt")

            batches = connection.execute(
                "SELECT x.*,o.content_sha256 AS object_sha,o.kind AS object_kind,"
                "o.state AS object_state,o.byte_size AS object_size "
                "FROM pubmed_bibliography_batches x "
                "LEFT JOIN object_registry o ON o.object_id=x.payload_object_id "
                "WHERE x.unit_id=? AND x.payload_object_id=? "
                "AND x.parser_version=? AND x.observed_at=? "
                "ORDER BY x.start_index,x.batch_offset",
                (
                    row["unit_id"],
                    row["payload_object_id"],
                    row["parser_version"],
                    row["observed_at"],
                ),
            ).fetchall()
            candidates = []
            for batch in batches:
                pmids = self._pmids(batch["pmids_json"])
                if row["native_id"] not in pmids:
                    continue
                page = connection.execute(
                    "SELECT pmids_json FROM pubmed_harvest_pages "
                    "WHERE unit_id=? AND start_index=?",
                    (row["unit_id"], batch["start_index"]),
                ).fetchone()
                if page is None:
                    raise Error("pubmed_observation_state_corrupt")
                page_pmids = self._pmids(page["pmids_json"])
                offset = batch["batch_offset"]
                if (
                    type(offset) is not int
                    or offset < 0
                    or page_pmids[offset : offset + len(pmids)] != pmids
                    or batch["parser_version"]
                    != PubmedSourceAdapter.PARSER_VERSION
                    or not isinstance(batch["response_sha256"], str)
                    or re.fullmatch(
                        r"[0-9a-f]{64}",
                        batch["response_sha256"],
                    )
                    is None
                    or row["payload_object_id"]
                    != "raw:" + batch["response_sha256"]
                    or batch["object_sha"] != batch["response_sha256"]
                    or batch["object_kind"] != "raw"
                    or batch["object_state"] != "available"
                    or type(batch["object_size"]) is not int
                    or batch["object_size"] < 0
                ):
                    raise Error("pubmed_observation_state_corrupt")
                candidates.append(
                    (
                        pmids,
                        batch["response_sha256"],
                        batch["object_size"],
                    )
                )
            if not candidates:
                raise Error("pubmed_observation_batch_missing")
            if len(candidates) != 1:
                raise Error("pubmed_observation_batch_ambiguous")
            pmids, response_sha256, byte_size = candidates[0]
            expected_id = "observation:" + hashlib.sha256(
                self._canonical(
                    [
                        row["unit_id"],
                        "pubmed",
                        row["native_id"],
                        row["payload_object_id"],
                        row["parser_version"],
                    ]
                ).encode("utf-8")
            ).hexdigest()
            if expected_id != row["id"]:
                raise Error("pubmed_observation_state_corrupt")
            return _ReplaySnapshot(
                row["id"],
                row["unit_id"],
                row["native_id"],
                row["payload_object_id"],
                row["parser_version"],
                self._instant(row["observed_at"]),
                pmids,
                response_sha256,
                byte_size,
            )

    @staticmethod
    def _request(snapshot: _ReplaySnapshot) -> SourcePageRequest:
        query = hashlib.sha256(
            SqlitePubmedObservationReplayAdapter._canonical(
                list(snapshot.pmids)
            ).encode("utf-8")
        ).hexdigest()
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
            + urlencode(
                [
                    ("db", "pubmed"),
                    ("id", ",".join(snapshot.pmids)),
                    ("retmode", "xml"),
                ]
            )
        )
        fingerprint = hashlib.sha256(
            (query + " GET " + url).encode("utf-8")
        ).hexdigest()
        return SourcePageRequest(
            "pubmed",
            query,
            fingerprint,
            "GET",
            url,
            0,
            len(snapshot.pmids),
            len(snapshot.pmids),
        )

    def __call__(self, observation_id: str) -> PubmedObservationReplay:
        identity = self._observation_id(observation_id)
        before = self._snapshot(identity)
        try:
            body = self._read_object(before.payload_object_id)
        except StorageError as exc:
            raise Error("pubmed_observation_raw_unavailable") from exc
        if (
            not isinstance(body, bytes)
            or len(body) != before.byte_size
            or hashlib.sha256(body).hexdigest() != before.response_sha256
        ):
            raise Error("pubmed_observation_raw_corrupt")
        request = self._request(before)
        try:
            batch = self._parser.parse_bibliography(
                request,
                body,
                http_status=200,
            )
        except SourceParseError as exc:
            raise Error("pubmed_observation_parse_failed") from exc
        if (
            batch.parser_version != before.parser_version
            or batch.response_sha256 != before.response_sha256
        ):
            raise Error("pubmed_observation_parse_mismatch")
        records = tuple(
            record for record in batch.records if record.pmid == before.native_id
        )
        if len(records) != 1:
            raise Error("pubmed_observation_record_mismatch")
        after = self._snapshot(identity)
        if after != before:
            raise Error("pubmed_observation_state_changed")
        return PubmedObservationReplay(
            before.observation_id,
            before.unit_id,
            before.payload_object_id,
            before.parser_version,
            before.observed_at,
            records[0],
        )
