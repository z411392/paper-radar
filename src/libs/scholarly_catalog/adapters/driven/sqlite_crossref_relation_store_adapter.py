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
    CrossrefRelationLifecycle,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


class SqliteCrossrefRelationStoreAdapter:
    _SNAPSHOT_PATH = "$snapshot"
    _COMPLETE_CODE = "relation_snapshot_complete"
    _INCOMPLETE_CODE = "relation_snapshot_incomplete"

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
    def _transaction(
        self,
        *,
        write: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise CrossrefProviderProjectionError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise CrossrefProviderProjectionError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
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
        *,
        source_canonical_doi: str | None = None,
        provider_revision_id: str | None = None,
        snapshot_complete: bool | None = None,
    ) -> None:
        if not isinstance(assertions, tuple) or not isinstance(gaps, tuple):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_relation_assertion"
            )
        authority_supplied = any(
            value is not None
            for value in (
                source_canonical_doi,
                provider_revision_id,
                snapshot_complete,
            )
        )
        if authority_supplied and (
            source_canonical_doi is None
            or provider_revision_id is None
            or type(snapshot_complete) is not bool
        ):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_relation_snapshot"
            )
        with self._transaction() as connection:
            if authority_supplied:
                source = self._text(source_canonical_doi, 512)
                if (
                    not isinstance(provider_revision_id, str)
                    or re.fullmatch(
                        r"crossref-provider-revision:[0-9a-f]{64}",
                        provider_revision_id,
                    )
                    is None
                ):
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_relation_snapshot"
                    )
                revision = connection.execute(
                    "SELECT canonical_doi,first_observed_at "
                    "FROM crossref_provider_revisions WHERE id=?",
                    (provider_revision_id,),
                ).fetchone()
                if revision is None or revision["canonical_doi"] != source:
                    raise CrossrefProviderProjectionError(
                        "crossref_relation_revision_mismatch"
                    )
                marker_code = (
                    self._COMPLETE_CODE
                    if snapshot_complete
                    else self._INCOMPLETE_CODE
                )
                marker_json = json.dumps(
                    {"complete": snapshot_complete},
                    sort_keys=True,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                existing_marker = connection.execute(
                    "SELECT error_code,raw_json FROM "
                    "crossref_relation_parse_gaps "
                    "WHERE provider_revision_id=? AND path=?",
                    (provider_revision_id, self._SNAPSHOT_PATH),
                ).fetchone()
                if existing_marker is None:
                    marker_id = "crossref-relation-gap:" + self._hash(
                        provider_revision_id,
                        self._SNAPSHOT_PATH,
                        marker_code,
                    )
                    connection.execute(
                        "INSERT INTO crossref_relation_parse_gaps "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            marker_id,
                            provider_revision_id,
                            self._SNAPSHOT_PATH,
                            marker_code,
                            marker_json,
                            revision["first_observed_at"],
                        ),
                    )
                elif tuple(existing_marker) != (marker_code, marker_json):
                    raise CrossrefProviderProjectionError(
                        "crossref_relation_snapshot_conflict"
                    )
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
                if path == self._SNAPSHOT_PATH:
                    raise CrossrefProviderProjectionError(
                        "invalid_crossref_relation_gap"
                    )
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


    def lifecycle(
        self,
        source_canonical_doi: str,
    ) -> tuple[CrossrefRelationLifecycle, ...]:
        source = self._text(source_canonical_doi, 512)
        complete_json = json.dumps(
            {"complete": True},
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        with self._transaction(write=False) as connection:
            assertions = connection.execute(
                "SELECT id,source_canonical_doi,predicate_raw,"
                "target_id_type_raw,target_value_raw,asserted_by_raw "
                "FROM crossref_relation_assertions "
                "WHERE source_canonical_doi=? ORDER BY id",
                (source,),
            ).fetchall()
            lifecycle = []
            for assertion in assertions:
                last = connection.execute(
                    "SELECT r.id,r.revision_no "
                    "FROM crossref_relation_revision_observations o "
                    "JOIN crossref_provider_revisions r "
                    "ON r.id=o.provider_revision_id "
                    "WHERE o.assertion_id=? AND r.canonical_doi=? "
                    "ORDER BY r.revision_no DESC LIMIT 1",
                    (assertion["id"], source),
                ).fetchone()
                if last is None:
                    raise CrossrefProviderProjectionError(
                        "crossref_relation_state_corrupt"
                    )
                withdrawn = connection.execute(
                    "SELECT r.id FROM crossref_provider_revisions r "
                    "WHERE r.canonical_doi=? AND r.revision_no>? "
                    "AND EXISTS("
                    "SELECT 1 FROM crossref_relation_parse_gaps marker "
                    "WHERE marker.provider_revision_id=r.id "
                    "AND marker.path=? AND marker.error_code=? "
                    "AND marker.raw_json=?"
                    ") "
                    "AND NOT EXISTS("
                    "SELECT 1 FROM crossref_relation_parse_gaps gap "
                    "WHERE gap.provider_revision_id=r.id AND gap.path<>?"
                    ") "
                    "AND NOT EXISTS("
                    "SELECT 1 FROM crossref_relation_revision_observations o "
                    "WHERE o.provider_revision_id=r.id AND o.assertion_id=?"
                    ") "
                    "ORDER BY r.revision_no LIMIT 1",
                    (
                        source,
                        last["revision_no"],
                        self._SNAPSHOT_PATH,
                        self._COMPLETE_CODE,
                        complete_json,
                        self._SNAPSHOT_PATH,
                        assertion["id"],
                    ),
                ).fetchone()
                lifecycle.append(
                    CrossrefRelationLifecycle(
                        assertion["id"],
                        assertion["source_canonical_doi"],
                        assertion["predicate_raw"],
                        assertion["target_id_type_raw"],
                        assertion["target_value_raw"],
                        assertion["asserted_by_raw"],
                        (
                            "no_longer_observed"
                            if withdrawn is not None
                            else "observed"
                        ),
                        last["id"],
                        None if withdrawn is None else withdrawn["id"],
                    )
                )
            return tuple(lifecycle)
