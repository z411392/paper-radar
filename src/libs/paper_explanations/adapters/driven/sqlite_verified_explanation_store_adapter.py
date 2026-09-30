import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.paper_explanations.domain.services.explanation_persistence_rules import (
    ExplanationPersistenceRules,
)
from libs.paper_explanations.dtos.explanation_persistence import (
    PersistedExplanation,
    PersistExplanationRequest,
    PreparedExplanation,
)
from libs.paper_explanations.exceptions.explanation_verification_error import (
    ExplanationVerificationError,
)


class SqliteVerifiedExplanationStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _canonical(value: object) -> str:
        try:
            text = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            text.encode("utf-8")
            return text
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise ExplanationVerificationError("invalid_summary_persistence") from exc

    @staticmethod
    def _time(value: datetime) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ExplanationVerificationError("invalid_summary_persistence")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError) as exc:
            raise ExplanationVerificationError("invalid_summary_persistence") from exc

    @staticmethod
    def _run_id(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"run:[0-9a-f]{64}", value) is None:
            raise ExplanationVerificationError("invalid_summary_model_run")
        return value

    @staticmethod
    def _fingerprint(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ExplanationVerificationError("invalid_summary_model_run")
        return value

    @staticmethod
    def _object_id(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"model_output:[0-9a-f]{64}", value) is None:
            raise ExplanationVerificationError("invalid_summary_object")
        return value

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.in_transaction:
                raise ExplanationVerificationError("owned_connection_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except sqlite3.IntegrityError as exc:
            raise ExplanationVerificationError("summary_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "summary_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "summary_database_error"
            )
            raise ExplanationVerificationError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _validate_model_run(
        cls,
        connection: sqlite3.Connection,
        run_id: str,
        fingerprint: str,
    ) -> None:
        run_id = cls._run_id(run_id)
        fingerprint = cls._fingerprint(fingerprint)
        row = connection.execute(
            "SELECT state,input_fingerprint FROM model_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ExplanationVerificationError("summary_model_run_missing")
        if row["state"] != "succeeded" or row["input_fingerprint"] != fingerprint:
            raise ExplanationVerificationError("summary_model_run_mismatch")

    @staticmethod
    def _validate_snapshot_and_anchors(
        connection: sqlite3.Connection,
        request: PersistExplanationRequest,
    ) -> None:
        draft = request.draft
        snapshot = connection.execute(
            "SELECT 1 FROM evidence_snapshots WHERE id=? AND revision_id=? AND work_id=?",
            (draft.snapshot_id, draft.revision_id, draft.work_id),
        ).fetchone()
        if snapshot is None:
            raise ExplanationVerificationError("summary_snapshot_mismatch")
        anchors: dict[str, tuple[str, str]] = {}
        for row in connection.execute(
            "SELECT id,snapshot_id,quote FROM evidence_anchors WHERE snapshot_id=?",
            (draft.snapshot_id,),
        ).fetchall():
            anchors[row["id"]] = (row["snapshot_id"], row["quote"])
        for claim in request.claims.claims:
            if len(claim.anchor_ids) != len(claim.source_quotes):
                raise ExplanationVerificationError("summary_claim_mismatch")
            for anchor_id, quote in zip(claim.anchor_ids, claim.source_quotes, strict=True):
                if anchors.get(anchor_id) != (draft.snapshot_id, quote):
                    raise ExplanationVerificationError("summary_claim_mismatch")

    @classmethod
    def _validate_database_input(
        cls,
        connection: sqlite3.Connection,
        request: PersistExplanationRequest,
    ) -> None:
        cls._validate_model_run(
            connection,
            request.claim_generation_run_id,
            request.claim_generation_fingerprint,
        )
        cls._validate_model_run(
            connection,
            request.reading_generation_run_id,
            request.reading_generation_fingerprint,
        )
        if request.support_generation_run_id is not None:
            assert request.support_generation_fingerprint is not None
            cls._validate_model_run(
                connection,
                request.support_generation_run_id,
                request.support_generation_fingerprint,
            )
        cls._validate_snapshot_and_anchors(connection, request)

    def validate(self, request: PersistExplanationRequest) -> None:
        ExplanationPersistenceRules.prepare(request)
        with self._transaction(write=False) as connection:
            self._validate_database_input(connection, request)

    @classmethod
    def _require_object(cls, connection: sqlite3.Connection, object_id: str) -> None:
        object_id = cls._object_id(object_id)
        row = connection.execute(
            "SELECT state FROM object_registry WHERE object_id=?",
            (object_id,),
        ).fetchone()
        if row is None or row["state"] != "available":
            raise ExplanationVerificationError("summary_object_missing")

    @classmethod
    def _persist_claims(
        cls,
        connection: sqlite3.Connection,
        request: PersistExplanationRequest,
    ) -> None:
        snapshot_id = request.draft.snapshot_id
        for claim in request.claims.claims:
            claim_json = cls._canonical(
                {
                    "format_version": 1,
                    "claim_type": claim.claim_type,
                    "anchor_ids": list(claim.anchor_ids),
                }
            )
            existing = connection.execute(
                "SELECT snapshot_id,extraction_run_id,claim_type,claim_json "
                "FROM paper_claims WHERE id=?",
                (claim.claim_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO paper_claims("
                    "id,snapshot_id,extraction_run_id,claim_type,claim_json"
                    ") VALUES(?,?,?,?,?)",
                    (
                        claim.claim_id,
                        snapshot_id,
                        request.claim_generation_run_id,
                        claim.claim_type,
                        claim_json,
                    ),
                )
            elif (
                existing["snapshot_id"] != snapshot_id
                or existing["claim_type"] != claim.claim_type
                or existing["claim_json"] != claim_json
            ):
                raise ExplanationVerificationError("summary_claim_conflict")

            for anchor_id in claim.anchor_ids:
                relation = connection.execute(
                    "SELECT snapshot_id FROM claim_anchors WHERE claim_id=? AND anchor_id=?",
                    (claim.claim_id, anchor_id),
                ).fetchone()
                if relation is None:
                    connection.execute(
                        "INSERT INTO claim_anchors(claim_id,anchor_id,snapshot_id) VALUES(?,?,?)",
                        (claim.claim_id, anchor_id, snapshot_id),
                    )
                elif relation["snapshot_id"] != snapshot_id:
                    raise ExplanationVerificationError("summary_claim_conflict")

    @staticmethod
    def _verification_id(summary_id: str, kind: str, report_object_id: str) -> str:
        digest = hashlib.sha256(
            f"{summary_id}\0{kind}\0{report_object_id}".encode("utf-8")
        ).hexdigest()
        return "verification:" + digest

    @classmethod
    def _persist_verification(
        cls,
        connection: sqlite3.Connection,
        *,
        summary_id: str,
        kind: str,
        run_id: str | None,
        report_object_id: str,
        verdict: str,
        verified_at: str,
    ) -> None:
        verification_id = cls._verification_id(summary_id, kind, report_object_id)
        existing = connection.execute(
            "SELECT summary_id,verifier_kind,run_id,report_object_id,verdict "
            "FROM verification_results WHERE id=?",
            (verification_id,),
        ).fetchone()
        expected = (summary_id, kind, run_id, report_object_id, verdict)
        if existing is None:
            connection.execute(
                "INSERT INTO verification_results("
                "id,summary_id,verifier_kind,run_id,report_object_id,verdict,verified_at"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    verification_id,
                    summary_id,
                    kind,
                    run_id,
                    report_object_id,
                    verdict,
                    verified_at,
                ),
            )
        elif tuple(existing) != expected:
            raise ExplanationVerificationError("summary_verification_conflict")

    @staticmethod
    def _qa_transition(existing: str, requested: str) -> str:
        if existing == requested:
            return existing
        if existing == "pending" and requested in {"passed", "rejected"}:
            return requested
        if existing in {"passed", "rejected"} and requested == "pending":
            return existing
        raise ExplanationVerificationError("summary_qa_conflict")

    def save(
        self,
        request: PersistExplanationRequest,
        prepared: PreparedExplanation,
        *,
        summary_object_id: str,
        deterministic_report_object_id: str,
        support_report_object_id: str | None,
    ) -> PersistedExplanation:
        checked = ExplanationPersistenceRules.prepare(request)
        if checked != prepared:
            raise ExplanationVerificationError("summary_preparation_mismatch")
        created_at = self._time(request.created_at)
        summary_object_id = self._object_id(summary_object_id)
        deterministic_report_object_id = self._object_id(deterministic_report_object_id)
        if support_report_object_id is not None:
            support_report_object_id = self._object_id(support_report_object_id)
        if (prepared.support_sql_verdict is None) != (support_report_object_id is None):
            raise ExplanationVerificationError("summary_preparation_mismatch")

        with self._transaction(write=True) as connection:
            self._validate_database_input(connection, request)
            self._require_object(connection, summary_object_id)
            self._require_object(connection, deterministic_report_object_id)
            if support_report_object_id is not None:
                self._require_object(connection, support_report_object_id)

            self._persist_claims(connection, request)

            existing = connection.execute(
                "SELECT * FROM summary_revisions WHERE id=? OR generation_fingerprint=?",
                (prepared.summary_id, request.reading_generation_fingerprint),
            ).fetchall()
            if len(existing) > 1:
                raise ExplanationVerificationError("summary_database_corrupt")
            effective_qa = prepared.qa_state
            if not existing:
                connection.execute(
                    "INSERT INTO summary_revisions("
                    "id,revision_id,work_id,snapshot_id,generation_fingerprint,"
                    "generation_run_id,output_object_id,language,explanation_profile,"
                    "qa_state,created_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        prepared.summary_id,
                        request.draft.revision_id,
                        request.draft.work_id,
                        request.draft.snapshot_id,
                        request.reading_generation_fingerprint,
                        request.reading_generation_run_id,
                        summary_object_id,
                        prepared.language,
                        prepared.explanation_profile,
                        prepared.qa_state,
                        created_at,
                    ),
                )
            else:
                row = existing[0]
                expected = (
                    prepared.summary_id,
                    request.draft.revision_id,
                    request.draft.work_id,
                    request.draft.snapshot_id,
                    request.reading_generation_fingerprint,
                    request.reading_generation_run_id,
                    summary_object_id,
                    prepared.language,
                    prepared.explanation_profile,
                )
                actual = (
                    row["id"],
                    row["revision_id"],
                    row["work_id"],
                    row["snapshot_id"],
                    row["generation_fingerprint"],
                    row["generation_run_id"],
                    row["output_object_id"],
                    row["language"],
                    row["explanation_profile"],
                )
                if actual != expected:
                    raise ExplanationVerificationError("summary_content_conflict")
                effective_qa = self._qa_transition(row["qa_state"], prepared.qa_state)
                if effective_qa != row["qa_state"]:
                    connection.execute(
                        "UPDATE summary_revisions SET qa_state=? WHERE id=?",
                        (effective_qa, prepared.summary_id),
                    )

            for index, statement in enumerate(request.draft.plain_language_card):
                locator = f"plain_language_card:{index}"
                for claim_id in statement.claim_ids:
                    existing_link = connection.execute(
                        "SELECT snapshot_id FROM summary_claims "
                        "WHERE summary_id=? AND claim_id=? AND output_locator=?",
                        (prepared.summary_id, claim_id, locator),
                    ).fetchone()
                    if existing_link is None:
                        connection.execute(
                            "INSERT INTO summary_claims("
                            "summary_id,claim_id,snapshot_id,output_locator"
                            ") VALUES(?,?,?,?)",
                            (
                                prepared.summary_id,
                                claim_id,
                                request.draft.snapshot_id,
                                locator,
                            ),
                        )
                    elif existing_link["snapshot_id"] != request.draft.snapshot_id:
                        raise ExplanationVerificationError("summary_claim_conflict")

            self._persist_verification(
                connection,
                summary_id=prepared.summary_id,
                kind="deterministic-v1",
                run_id=None,
                report_object_id=deterministic_report_object_id,
                verdict=request.verification.deterministic.verdict,
                verified_at=created_at,
            )

            if support_report_object_id is not None:
                assert request.support_generation_run_id is not None
                assert prepared.support_sql_verdict is not None
                self._persist_verification(
                    connection,
                    summary_id=prepared.summary_id,
                    kind="supportiveness-v1",
                    run_id=request.support_generation_run_id,
                    report_object_id=support_report_object_id,
                    verdict=prepared.support_sql_verdict,
                    verified_at=created_at,
                )

            return PersistedExplanation(
                prepared.summary_id,
                request.draft.snapshot_id,
                request.draft.revision_id,
                request.draft.work_id,
                request.reading_generation_fingerprint,
                request.reading_generation_run_id,
                summary_object_id,
                effective_qa,
                prepared.language,
                prepared.explanation_profile,
                True,
            )
