import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone

from libs.paper_explanations.domain.services.explanation_verification_rules import (
    ExplanationVerificationRules,
)
from libs.paper_explanations.dtos.explanation_persistence import (
    PersistExplanationRequest,
    PreparedExplanation,
)
from libs.paper_explanations.dtos.explanation_verification import (
    SupportVerificationCandidate,
)
from libs.paper_explanations.exceptions.explanation_verification_error import (
    ExplanationVerificationError,
)

_EXPLANATION_PROFILE = "plain-zh-TW-v1"


class ExplanationPersistenceRules:
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
    def _fingerprint(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ExplanationVerificationError("invalid_summary_persistence")
        return value

    @staticmethod
    def _run_id(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"run:[0-9a-f]{64}", value) is None:
            raise ExplanationVerificationError("invalid_summary_persistence")
        return value

    @staticmethod
    def _time(value: object) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ExplanationVerificationError("invalid_summary_persistence")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError) as exc:
            raise ExplanationVerificationError("invalid_summary_persistence") from exc

    @classmethod
    def _validate_run_pairs(cls, request: PersistExplanationRequest) -> None:
        cls._run_id(request.claim_generation_run_id)
        cls._fingerprint(request.claim_generation_fingerprint)
        cls._run_id(request.reading_generation_run_id)
        cls._fingerprint(request.reading_generation_fingerprint)
        support_pair = (
            request.support_generation_run_id is not None,
            request.support_generation_fingerprint is not None,
        )
        if support_pair not in {(False, False), (True, True)}:
            raise ExplanationVerificationError("invalid_summary_persistence")
        if support_pair == (True, True):
            cls._run_id(request.support_generation_run_id)
            cls._fingerprint(request.support_generation_fingerprint)

    @classmethod
    def prepare(cls, request: PersistExplanationRequest) -> PreparedExplanation:
        if not isinstance(request, PersistExplanationRequest):
            raise ExplanationVerificationError("invalid_summary_persistence")
        cls._validate_run_pairs(request)
        created_at = cls._time(request.created_at)
        draft = request.draft
        claims = request.claims
        verification = request.verification

        deterministic = ExplanationVerificationRules.deterministic(draft, claims)
        if deterministic != verification.deterministic:
            raise ExplanationVerificationError("verification_result_mismatch")

        support_sql_verdict: str | None = None
        if deterministic.verdict == "rejected":
            if (
                verification.qa_state != "rejected"
                or verification.support is not None
                or verification.support_execution_state != "not_run"
                or request.support_generation_run_id is not None
            ):
                raise ExplanationVerificationError("verification_result_mismatch")
        elif deterministic.verdict == "passed":
            if verification.support_execution_state == "not_run":
                if (
                    verification.qa_state != "passed"
                    or verification.support is not None
                    or request.support_generation_run_id is not None
                    or request.support_generation_fingerprint is not None
                ):
                    raise ExplanationVerificationError("verification_result_mismatch")
            elif verification.support_execution_state == "failed":
                if (
                    verification.qa_state != "pending"
                    or verification.support is not None
                    or request.support_generation_run_id is not None
                ):
                    raise ExplanationVerificationError("verification_result_mismatch")
            elif verification.support_execution_state == "succeeded":
                if (
                    verification.support is None
                    or request.support_generation_run_id is None
                    or request.support_generation_fingerprint is None
                ):
                    raise ExplanationVerificationError("verification_result_mismatch")
                support_request = ExplanationVerificationRules.support_request(
                    draft,
                    claims,
                    deterministic,
                )
                parsed = ExplanationVerificationRules.parse_support(
                    support_request,
                    SupportVerificationCandidate(
                        verification.support.snapshot_id,
                        verification.support.input_fingerprint,
                        verification.support.statements,
                    ),
                )
                if parsed != verification.support:
                    raise ExplanationVerificationError("verification_result_mismatch")
                verdicts = {item.verdict for item in parsed.statements}
                expected_qa = (
                    "rejected"
                    if "unsupported" in verdicts
                    else "pending"
                    if "uncertain" in verdicts
                    else "passed"
                )
                if verification.qa_state != expected_qa:
                    raise ExplanationVerificationError("verification_result_mismatch")
                if "unsupported" in verdicts:
                    support_sql_verdict = "rejected"
                elif "uncertain" not in verdicts:
                    support_sql_verdict = "passed"
            else:
                raise ExplanationVerificationError("verification_result_mismatch")
        else:
            raise ExplanationVerificationError("verification_result_mismatch")

        if verification.qa_state not in {"pending", "passed", "rejected"}:
            raise ExplanationVerificationError("verification_result_mismatch")
        if (
            draft.target_language != "zh-TW"
            or draft.evidence_level != "abstract_only"
            or draft.validation_state != "draft_requires_verification"
        ):
            raise ExplanationVerificationError("invalid_summary_persistence")

        summary_payload = {
            "format_version": 1,
            "snapshot_id": draft.snapshot_id,
            "revision_id": draft.revision_id,
            "work_id": draft.work_id,
            "generation_fingerprint": request.reading_generation_fingerprint,
            "generation_input_fingerprint": draft.input_fingerprint,
            "language": draft.target_language,
            "explanation_profile": _EXPLANATION_PROFILE,
            "evidence_level": draft.evidence_level,
            "original_abstract": draft.original_abstract,
            "faithful_translation": [asdict(item) for item in draft.faithful_translation],
            "plain_language_card": [asdict(item) for item in draft.plain_language_card],
            "not_reported_in_read_evidence": list(draft.not_reported_in_read_evidence),
            "claim_ids": [claim.claim_id for claim in claims.claims],
        }
        summary_text = cls._canonical(summary_payload)
        summary_id = "summary:" + hashlib.sha256(
            ("verified-summary-v1\0" + summary_text).encode("utf-8")
        ).hexdigest()

        deterministic_report = cls._canonical(
            {
                "format_version": 1,
                "verifier_kind": "deterministic-v1",
                "summary_id": summary_id,
                "report": asdict(deterministic),
                "verified_at": created_at,
            }
        ).encode("utf-8")

        support_report: bytes | None = None
        if verification.support is not None:
            support_report = cls._canonical(
                {
                    "format_version": 1,
                    "verifier_kind": "supportiveness-v1",
                    "summary_id": summary_id,
                    "execution_state": verification.support_execution_state,
                    "report": asdict(verification.support),
                    "verified_at": created_at,
                }
            ).encode("utf-8")

        return PreparedExplanation(
            summary_id,
            verification.qa_state,
            draft.target_language,
            _EXPLANATION_PROFILE,
            summary_text.encode("utf-8"),
            deterministic_report,
            support_report,
            support_sql_verdict,
        )
