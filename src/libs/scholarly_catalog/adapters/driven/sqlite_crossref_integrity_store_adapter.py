import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityAssertionDraft,
    CrossrefIntegrityAssertionRef,
    CrossrefIntegrityGapDraft,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


class SqliteCrossrefIntegrityStoreAdapter:
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
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_assertion"
            )
        try:
            if value.utcoffset() is None:
                raise ValueError
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_assertion"
            ) from None

    @staticmethod
    def _text(
        value: object,
        maximum: int,
        *,
        nullable: bool = False,
    ) -> str | None:
        if value is None and nullable:
            return None
        if not isinstance(value, str) or not value or value != value.strip():
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_assertion"
            )
        try:
            if len(value.encode("utf-8")) > maximum:
                raise CrossrefProviderProjectionError(
                    "invalid_crossref_integrity_assertion"
                )
        except UnicodeEncodeError:
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_assertion"
            ) from None
        return value

    @staticmethod
    def _canonical_json(value: str, code: str) -> str:
        try:
            parsed = json.loads(value)
            canonical = json.dumps(
                parsed,
                sort_keys=True,
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            raise CrossrefProviderProjectionError(code) from None
        if canonical != value:
            raise CrossrefProviderProjectionError(code)
        return canonical

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
                "crossref_integrity_database_conflict"
            ) from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "crossref_integrity_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "crossref_integrity_database_error"
            )
            raise CrossrefProviderProjectionError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _assertion_id(cls, draft: CrossrefIntegrityAssertionDraft) -> str:
        return "crossref-integrity:" + cls._hash(
            draft.record_canonical_doi,
            draft.wire_direction,
            draft.raw_json,
        )

    def register(
        self,
        assertions: tuple[CrossrefIntegrityAssertionDraft, ...],
        gaps: tuple[CrossrefIntegrityGapDraft, ...],
    ) -> tuple[CrossrefIntegrityAssertionRef, ...]:
        if not isinstance(assertions, tuple) or not isinstance(gaps, tuple):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_assertion"
            )
        refs: list[CrossrefIntegrityAssertionRef] = []
        with self._transaction() as connection:
            for draft in assertions:
                if not isinstance(draft, CrossrefIntegrityAssertionDraft):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_integrity_assertion"
                    )
                record_doi = self._text(draft.record_canonical_doi, 512)
                counterparty_raw = self._text(
                    draft.counterparty_doi_raw,
                    2048,
                    nullable=True,
                )
                counterparty_canonical = self._text(
                    draft.counterparty_canonical_doi,
                    512,
                    nullable=True,
                )
                notice_doi = self._text(
                    draft.notice_canonical_doi,
                    512,
                    nullable=True,
                )
                target_doi = self._text(
                    draft.target_canonical_doi,
                    512,
                    nullable=True,
                )
                type_raw = self._text(draft.type_raw, 256, nullable=True)
                source_raw = self._text(draft.source_raw, 256, nullable=True)
                label_raw = self._text(draft.label_raw, 1024, nullable=True)
                updated_value = self._text(
                    draft.updated_value,
                    128,
                    nullable=True,
                )
                record_id = (
                    None
                    if draft.record_id_raw_json is None
                    else self._canonical_json(
                        draft.record_id_raw_json,
                        "invalid_crossref_integrity_assertion",
                    )
                )
                updated_raw = (
                    None
                    if draft.updated_raw_json is None
                    else self._canonical_json(
                        draft.updated_raw_json,
                        "invalid_crossref_integrity_assertion",
                    )
                )
                raw_json = self._canonical_json(
                    draft.raw_json,
                    "invalid_crossref_integrity_assertion",
                )
                if (
                    draft.wire_direction not in {"update_to", "updated_by"}
                    or type(draft.update_ordinal) is not int
                    or draft.update_ordinal < 0
                    or draft.counterparty_normalization_state
                    not in {"normalized", "invalid", "missing"}
                    or draft.event_class
                    not in {
                        "correction",
                        "retraction",
                        "expression_of_concern",
                        "reinstatement",
                        "other_update",
                    }
                    or draft.updated_precision
                    not in {None, "year", "month", "day", "second"}
                ):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_integrity_assertion"
                    )
                state = draft.counterparty_normalization_state
                if state == "normalized" and (
                    counterparty_raw is None or counterparty_canonical is None
                ):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_integrity_assertion"
                    )
                if state == "invalid" and (
                    counterparty_raw is None or counterparty_canonical is not None
                ):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_integrity_assertion"
                    )
                if state == "missing" and (
                    counterparty_raw is not None or counterparty_canonical is not None
                ):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_integrity_assertion"
                    )
                if draft.wire_direction == "update_to":
                    valid_direction = (
                        notice_doi == record_doi
                        and target_doi == counterparty_canonical
                    )
                else:
                    valid_direction = (
                        target_doi == record_doi
                        and notice_doi == counterparty_canonical
                    )
                if not valid_direction:
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_integrity_assertion"
                    )
                observed = self._time(draft.observed_at)
                revision = connection.execute(
                    "SELECT canonical_doi FROM crossref_provider_revisions "
                    "WHERE id=?",
                    (draft.provider_revision_id,),
                ).fetchone()
                if revision is None or revision["canonical_doi"] != record_doi:
                    raise CrossrefProviderProjectionError(
                        "crossref_integrity_revision_mismatch"
                    )
                assertion_id = self._assertion_id(draft)
                expected = (
                    assertion_id,
                    record_doi,
                    draft.wire_direction,
                    counterparty_raw,
                    counterparty_canonical,
                    state,
                    notice_doi,
                    target_doi,
                    type_raw,
                    source_raw,
                    label_raw,
                    record_id,
                    draft.event_class,
                    updated_value,
                    draft.updated_precision,
                    updated_raw,
                    raw_json,
                    observed,
                )
                existing = connection.execute(
                    "SELECT * FROM crossref_integrity_assertions WHERE id=?",
                    (assertion_id,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO crossref_integrity_assertions "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        expected,
                    )
                elif tuple(existing)[:-1] != expected[:-1]:
                    raise CrossrefProviderProjectionError(
                        "crossref_integrity_assertion_conflict"
                    )

                observation = connection.execute(
                    "SELECT assertion_id FROM "
                    "crossref_integrity_revision_observations "
                    "WHERE provider_revision_id=? AND wire_direction=? "
                    "AND update_ordinal=?",
                    (
                        draft.provider_revision_id,
                        draft.wire_direction,
                        draft.update_ordinal,
                    ),
                ).fetchone()
                if observation is None:
                    connection.execute(
                        "INSERT INTO crossref_integrity_revision_observations "
                        "VALUES(?,?,?,?,?)",
                        (
                            draft.provider_revision_id,
                            draft.wire_direction,
                            draft.update_ordinal,
                            assertion_id,
                            observed,
                        ),
                    )
                elif observation["assertion_id"] != assertion_id:
                    raise CrossrefProviderProjectionError(
                        "crossref_integrity_observation_conflict"
                    )
                refs.append(
                    CrossrefIntegrityAssertionRef(
                        assertion_id,
                        notice_doi,
                        target_doi,
                    )
                )

            for gap in gaps:
                if not isinstance(gap, CrossrefIntegrityGapDraft):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_integrity_gap"
                    )
                path = self._text(gap.path, 512)
                error = self._text(gap.error_code, 128)
                raw = self._canonical_json(
                    gap.raw_json,
                    "invalid_crossref_integrity_gap",
                )
                observed = self._time(gap.observed_at)
                if connection.execute(
                    "SELECT 1 FROM crossref_provider_revisions WHERE id=?",
                    (gap.provider_revision_id,),
                ).fetchone() is None:
                    raise CrossrefProviderProjectionError(
                        "crossref_integrity_revision_mismatch"
                    )
                gap_id = "crossref-integrity-gap:" + self._hash(
                    gap.provider_revision_id,
                    path,
                    error,
                )
                existing = connection.execute(
                    "SELECT path,error_code,raw_json FROM "
                    "crossref_integrity_parse_gaps WHERE id=?",
                    (gap_id,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO crossref_integrity_parse_gaps "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            gap_id,
                            gap.provider_revision_id,
                            path,
                            error,
                            raw,
                            observed,
                        ),
                    )
                elif tuple(existing) != (path, error, raw):
                    raise CrossrefProviderProjectionError(
                        "crossref_integrity_gap_conflict"
                    )

        return tuple(refs)
