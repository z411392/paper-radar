import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.scholarly_catalog.dtos.crossref_relation_assertion import (
    CrossrefRelationAssertionDraft,
    CrossrefRelationGapDraft,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


class SqliteCrossrefRelationStoreAdapter:
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
            raise CrossrefProviderProjectionError(
                "invalid_crossref_relation_assertion"
            )
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_relation_assertion"
            ) from None

    @staticmethod
    def _text(value: object, maximum: int, *, nullable: bool = False) -> str | None:
        if value is None and nullable:
            return None
        if not isinstance(value, str) or not value or value != value.strip():
            raise CrossrefProviderProjectionError(
                "invalid_crossref_relation_assertion"
            )
        try:
            if len(value.encode("utf-8")) > maximum:
                raise CrossrefProviderProjectionError(
                    "invalid_crossref_relation_assertion"
                )
        except UnicodeEncodeError:
            raise CrossrefProviderProjectionError(
                "invalid_crossref_relation_assertion"
            ) from None
        return value

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
                "crossref_relation_database_conflict"
            ) from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "crossref_relation_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "crossref_relation_database_error"
            )
            raise CrossrefProviderProjectionError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _assertion_id(cls, draft: CrossrefRelationAssertionDraft) -> str:
        return "crossref-relation:" + cls._hash(
            draft.source_canonical_doi,
            draft.predicate_raw,
            draft.target_id_type_raw,
            draft.target_value_raw,
            draft.asserted_by_raw,
        )

    def register(
        self,
        assertions: tuple[CrossrefRelationAssertionDraft, ...],
        gaps: tuple[CrossrefRelationGapDraft, ...],
    ) -> None:
        if not isinstance(assertions, tuple) or not isinstance(gaps, tuple):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_relation_assertion"
            )
        with self._transaction() as connection:
            for draft in assertions:
                if not isinstance(draft, CrossrefRelationAssertionDraft):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_relation_assertion"
                    )
                source = self._text(draft.source_canonical_doi, 512)
                predicate = self._text(draft.predicate_raw, 256)
                id_type = self._text(draft.target_id_type_raw, 128)
                target = self._text(draft.target_value_raw, 4096)
                asserted = self._text(
                    draft.asserted_by_raw,
                    128,
                    nullable=True,
                )
                if (
                    type(draft.ordinal) is not int
                    or draft.ordinal < 0
                    or draft.relation_class
                    not in {"intra_work", "inter_work", "unknown"}
                    or draft.target_normalization_state
                    not in {"normalized", "raw", "invalid"}
                ):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_relation_assertion"
                    )
                if draft.target_normalization_state == "normalized":
                    namespace = self._text(draft.target_namespace, 64)
                    normalized = self._text(
                        draft.target_normalized_value,
                        4096,
                    )
                else:
                    namespace = normalized = None
                    if (
                        draft.target_namespace is not None
                        or draft.target_normalized_value is not None
                    ):
                        raise CrossrefProviderProjectionError(
                            "invalid_crossref_relation_assertion"
                        )
                observed = self._time(draft.observed_at)
                revision = connection.execute(
                    "SELECT canonical_doi FROM crossref_provider_revisions "
                    "WHERE id=?",
                    (draft.provider_revision_id,),
                ).fetchone()
                if revision is None or revision["canonical_doi"] != source:
                    raise CrossrefProviderProjectionError(
                        "crossref_relation_revision_mismatch"
                    )
                assertion_id = self._assertion_id(draft)
                existing = connection.execute(
                    "SELECT * FROM crossref_relation_assertions WHERE id=?",
                    (assertion_id,),
                ).fetchone()
                expected = (
                    assertion_id,
                    source,
                    predicate,
                    id_type,
                    target,
                    asserted,
                    draft.relation_class,
                    namespace,
                    normalized,
                    draft.target_normalization_state,
                    observed,
                )
                if existing is None:
                    connection.execute(
                        "INSERT INTO crossref_relation_assertions "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        expected,
                    )
                elif tuple(existing)[:-1] != expected[:-1]:
                    raise CrossrefProviderProjectionError(
                        "crossref_relation_assertion_conflict"
                    )

                observation = connection.execute(
                    "SELECT assertion_id FROM "
                    "crossref_relation_revision_observations "
                    "WHERE provider_revision_id=? AND ordinal=?",
                    (draft.provider_revision_id, draft.ordinal),
                ).fetchone()
                if observation is None:
                    connection.execute(
                        "INSERT INTO crossref_relation_revision_observations "
                        "VALUES(?,?,?,?)",
                        (
                            draft.provider_revision_id,
                            draft.ordinal,
                            assertion_id,
                            observed,
                        ),
                    )
                elif observation["assertion_id"] != assertion_id:
                    raise CrossrefProviderProjectionError(
                        "crossref_relation_observation_conflict"
                    )

            for gap in gaps:
                if not isinstance(gap, CrossrefRelationGapDraft):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_relation_gap"
                    )
                path = self._text(gap.path, 512)
                error = self._text(gap.error_code, 128)
                observed = self._time(gap.observed_at)
                try:
                    raw = json.loads(gap.raw_json)
                    canonical = json.dumps(
                        raw,
                        sort_keys=True,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                except (
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                    RecursionError,
                ):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_relation_gap"
                    ) from None
                if canonical != gap.raw_json:
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_relation_gap"
                    )
                if connection.execute(
                    "SELECT 1 FROM crossref_provider_revisions WHERE id=?",
                    (gap.provider_revision_id,),
                ).fetchone() is None:
                    raise CrossrefProviderProjectionError(
                        "crossref_relation_revision_mismatch"
                    )
                gap_id = "crossref-relation-gap:" + self._hash(
                    gap.provider_revision_id,
                    path,
                    error,
                )
                existing = connection.execute(
                    "SELECT path,error_code,raw_json FROM "
                    "crossref_relation_parse_gaps WHERE id=?",
                    (gap_id,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO crossref_relation_parse_gaps "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            gap_id,
                            gap.provider_revision_id,
                            path,
                            error,
                            canonical,
                            observed,
                        ),
                    )
                elif tuple(existing) != (path, error, canonical):
                    raise CrossrefProviderProjectionError(
                        "crossref_relation_gap_conflict"
                    )
