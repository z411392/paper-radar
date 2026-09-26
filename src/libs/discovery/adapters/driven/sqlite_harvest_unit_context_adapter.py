import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from libs.discovery.dtos.harvest_unit_context import HarvestUnitContext
from libs.discovery.exceptions.harvest_unit_context_error import HarvestUnitContextError as Error


class SqliteHarvestUnitContextAdapter:
    _SOURCES = frozenset({"arxiv", "pubmed"})

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise Error("owned_connection_required")
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
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "harvest_unit_context_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "harvest_unit_context_database_error"
            )
            raise Error(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in rows:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    @classmethod
    def _decode(cls, value: object) -> dict[str, object]:
        if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 1_048_576:
            raise Error("harvest_unit_context_corrupt")
        try:
            data = json.loads(
                value,
                object_pairs_hook=cls._pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
            canonical = json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Error("harvest_unit_context_corrupt") from None
        if not isinstance(data, dict) or canonical != value:
            raise Error("harvest_unit_context_corrupt")
        return data

    @staticmethod
    def _id(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
            raise Error("harvest_unit_context_corrupt")
        return value

    @staticmethod
    def _revision(value: object) -> int:
        if type(value) is not int or not 1 <= value < 2**63:
            raise Error("harvest_unit_context_corrupt")
        return value

    def __call__(self, unit_id: str, source: str) -> HarvestUnitContext:
        if not isinstance(unit_id, str) or re.fullmatch(r"unit:[0-9a-f]{64}", unit_id) is None:
            raise Error("invalid_harvest_unit_context")
        if source not in self._SOURCES:
            raise Error("invalid_harvest_unit_context")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT u.id,u.state,b.source,b.profile_id,b.profile_revision,"
                "b.config_revision,b.compiled_query_json,b.query_fingerprint "
                "FROM harvest_units u JOIN source_bindings b ON b.id=u.binding_id "
                "WHERE u.id=?",
                (unit_id,),
            ).fetchone()
            if row is None:
                raise Error("harvest_unit_context_missing")
            if row["state"] not in {"succeeded", "verified_empty"} or row["source"] != source:
                raise Error("harvest_unit_context_not_closed")
            compiled = row["compiled_query_json"]
            if (
                not isinstance(compiled, str)
                or hashlib.sha256(compiled.encode("utf-8")).hexdigest()
                != row["query_fingerprint"]
            ):
                raise Error("harvest_unit_context_corrupt")
            data = self._decode(compiled)
            input_data = data.get("input")
            domain = input_data.get("domain") if isinstance(input_data, dict) else None
            if not isinstance(input_data, dict) or not isinstance(domain, dict):
                raise Error("harvest_unit_context_corrupt")
            profile_id = self._id(input_data.get("profile_id"))
            profile_revision = self._revision(input_data.get("profile_revision"))
            domain_id = self._id(domain.get("id"))
            domain_revision = self._revision(domain.get("revision"))
            if (
                input_data.get("source_id") != source
                or profile_id != row["profile_id"]
                or profile_revision != row["profile_revision"]
                or domain_revision != row["config_revision"]
            ):
                raise Error("harvest_unit_context_corrupt")
            return HarvestUnitContext(
                unit_id,
                source,
                profile_id,
                profile_revision,
                domain_id,
                domain_revision,
            )
