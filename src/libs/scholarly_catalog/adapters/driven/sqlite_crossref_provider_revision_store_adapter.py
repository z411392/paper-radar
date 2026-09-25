import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.scholarly_catalog.dtos.crossref_provider_revision import (
    CrossrefProviderProjection,
    CrossrefProviderRevisionDraft,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


class SqliteCrossrefProviderRevisionStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _hash(*parts: object) -> str:
        payload = json.dumps(
            parts,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _time(value: object) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise CrossrefProviderProjectionError("invalid_crossref_provider_time")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_provider_time"
            ) from None

    @staticmethod
    def _text(value: object, code: str, maximum: int) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise CrossrefProviderProjectionError(code)
        try:
            if len(value.encode("utf-8")) > maximum:
                raise CrossrefProviderProjectionError(code)
        except UnicodeEncodeError:
            raise CrossrefProviderProjectionError(code) from None
        return value

    @classmethod
    def _validate(cls, draft: CrossrefProviderRevisionDraft) -> str:
        if not isinstance(draft, CrossrefProviderRevisionDraft):
            raise CrossrefProviderProjectionError("invalid_crossref_provider_revision")
        cls._text(draft.canonical_doi, "invalid_crossref_provider_revision", 512)
        cls._text(draft.raw_doi, "invalid_crossref_provider_revision", 2048)
        cls._text(draft.page_id, "invalid_crossref_provider_revision", 256)
        cls._text(draft.semantic_version, "invalid_crossref_provider_revision", 128)
        if (
            re.fullmatch(r"[0-9a-f]{64}", draft.provider_sha256) is None
            or re.fullmatch(r"[0-9a-f]{64}", draft.semantic_sha256) is None
            or type(draft.ordinal) is not int
            or draft.ordinal < 0
            or draft.published_precision not in {None, "year", "month", "day"}
        ):
            raise CrossrefProviderProjectionError("invalid_crossref_provider_revision")
        try:
            decoded = json.loads(draft.canonical_json)
            canonical = json.dumps(
                decoded,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            warnings_json = json.dumps(
                list(draft.parse_warnings),
                ensure_ascii=True,
                separators=(",", ":"),
            )
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_provider_revision"
            ) from None
        if (
            canonical != draft.canonical_json
            or hashlib.sha256(canonical.encode("ascii")).hexdigest()
            != draft.provider_sha256
        ):
            raise CrossrefProviderProjectionError("crossref_provider_item_mismatch")
        return warnings_json

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise CrossrefProviderProjectionError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise CrossrefProviderProjectionError("foreign_keys_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except CrossrefProviderProjectionError:
            raise
        except sqlite3.IntegrityError as exc:
            raise CrossrefProviderProjectionError(
                "crossref_provider_database_conflict"
            ) from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "crossref_provider_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "crossref_provider_database_error"
            )
            raise CrossrefProviderProjectionError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _result(row: sqlite3.Row, *, replayed: bool) -> CrossrefProviderProjection:
        return CrossrefProviderProjection(
            row["canonical_doi"],
            row["id"],
            row["semantic_sha256"],
            row["title"],
            row["published_date"],
            row["published_precision"],
            replayed,
        )

    def register(
        self,
        draft: CrossrefProviderRevisionDraft,
    ) -> CrossrefProviderProjection:
        warnings_json = self._validate(draft)
        observed = self._time(draft.observed_at)
        revision_id = "crossref-provider-revision:" + self._hash(
            draft.canonical_doi,
            draft.provider_sha256,
        )
        observation_id = "crossref-provider-observation:" + self._hash(
            draft.page_id,
            draft.ordinal,
        )
        with self._transaction() as connection:
            observation = connection.execute(
                "SELECT * FROM crossref_provider_observations "
                "WHERE page_id=? AND ordinal=?",
                (draft.page_id, draft.ordinal),
            ).fetchone()
            if observation is not None:
                revision = connection.execute(
                    "SELECT * FROM crossref_provider_revisions WHERE id=?",
                    (observation["provider_revision_id"],),
                ).fetchone()
                if (
                    revision is None
                    or revision["id"] != revision_id
                    or observation["canonical_doi"] != draft.canonical_doi
                ):
                    raise CrossrefProviderProjectionError(
                        "crossref_provider_observation_conflict"
                    )
                return self._result(revision, replayed=True)

            record = connection.execute(
                "SELECT * FROM crossref_source_records WHERE canonical_doi=?",
                (draft.canonical_doi,),
            ).fetchone()
            if record is None:
                connection.execute(
                    "INSERT INTO crossref_source_records VALUES(?,?,?)",
                    (draft.canonical_doi, draft.raw_doi, observed),
                )

            revision = connection.execute(
                "SELECT * FROM crossref_provider_revisions "
                "WHERE canonical_doi=? AND provider_sha256=?",
                (draft.canonical_doi, draft.provider_sha256),
            ).fetchone()
            replayed = revision is not None
            if revision is None:
                revision_no = connection.execute(
                    "SELECT COALESCE(MAX(revision_no),0)+1 "
                    "FROM crossref_provider_revisions WHERE canonical_doi=?",
                    (draft.canonical_doi,),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO crossref_provider_revisions VALUES("
                    "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        revision_id,
                        draft.canonical_doi,
                        revision_no,
                        draft.raw_doi,
                        draft.provider_sha256,
                        draft.semantic_sha256,
                        draft.semantic_version,
                        draft.canonical_json,
                        draft.title,
                        draft.indexed_at,
                        draft.created_at,
                        draft.deposited_at,
                        draft.published_date,
                        draft.published_precision,
                        warnings_json,
                        observed,
                    ),
                )
                revision = connection.execute(
                    "SELECT * FROM crossref_provider_revisions WHERE id=?",
                    (revision_id,),
                ).fetchone()
                assert revision is not None

            connection.execute(
                "INSERT INTO crossref_provider_observations VALUES(?,?,?,?,?,?)",
                (
                    observation_id,
                    revision["id"],
                    draft.canonical_doi,
                    draft.page_id,
                    draft.ordinal,
                    observed,
                ),
            )
            return self._result(revision, replayed=replayed)
