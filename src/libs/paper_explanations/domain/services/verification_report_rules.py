import json
import re
from dataclasses import asdict

from libs.paper_explanations.dtos.explanation_verification import ExplanationVerificationResult
from libs.paper_explanations.exceptions.explanation_verification_error import ExplanationVerificationError


class VerificationReportRules:
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
            raise ExplanationVerificationError("invalid_verification_result") from exc

    @staticmethod
    def _hex(value: object, prefix: str) -> None:
        if not isinstance(value, str) or re.fullmatch(prefix + r":[0-9a-f]{64}", value) is None:
            raise ExplanationVerificationError("invalid_verification_result")

    @classmethod
    def validate(cls, result: ExplanationVerificationResult) -> None:
        if not isinstance(result, ExplanationVerificationResult):
            raise ExplanationVerificationError("invalid_verification_result")
        deterministic = result.deterministic
        cls._hex(deterministic.snapshot_id, "snapshot")
        cls._hex(deterministic.revision_id, "revision")
        cls._hex(deterministic.work_id, "work")
        if (
            not isinstance(deterministic.input_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", deterministic.input_fingerprint) is None
            or deterministic.verdict not in {"passed", "rejected"}
            or result.qa_state not in {"pending", "passed", "rejected"}
            or result.support_execution_state not in {"not_run", "failed", "succeeded"}
        ):
            raise ExplanationVerificationError("invalid_verification_result")
        if deterministic.verdict == "passed" and deterministic.findings:
            raise ExplanationVerificationError("invalid_verification_result")
        if deterministic.verdict == "rejected" and not deterministic.findings:
            raise ExplanationVerificationError("invalid_verification_result")

        support = result.support
        if deterministic.verdict == "rejected":
            if (
                support is not None
                or result.support_execution_state != "not_run"
                or result.qa_state != "rejected"
            ):
                raise ExplanationVerificationError("invalid_verification_result")
            return
        if result.support_execution_state == "failed":
            if support is not None or result.qa_state != "pending":
                raise ExplanationVerificationError("invalid_verification_result")
            return
        if result.support_execution_state != "succeeded" or support is None:
            raise ExplanationVerificationError("invalid_verification_result")
        if (
            support.snapshot_id != deterministic.snapshot_id
            or not isinstance(support.input_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", support.input_fingerprint) is None
            or not support.statements
        ):
            raise ExplanationVerificationError("invalid_verification_result")
        verdicts = []
        seen: set[int] = set()
        for item in support.statements:
            if (
                type(item.statement_index) is not int
                or item.statement_index < 0
                or item.statement_index in seen
                or item.verdict not in {"supported", "unsupported", "uncertain"}
                or not isinstance(item.claim_ids, tuple)
                or not item.claim_ids
                or len(item.claim_ids) != len(set(item.claim_ids))
            ):
                raise ExplanationVerificationError("invalid_verification_result")
            seen.add(item.statement_index)
            for claim_id in item.claim_ids:
                cls._hex(claim_id, "claim")
            verdicts.append(item.verdict)
        expected = (
            "rejected"
            if "unsupported" in verdicts
            else "pending"
            if "uncertain" in verdicts
            else "passed"
        )
        if result.qa_state != expected:
            raise ExplanationVerificationError("invalid_verification_result")

    @classmethod
    def serialize(cls, summary_id: str, result: ExplanationVerificationResult) -> bytes:
        if not isinstance(summary_id, str) or re.fullmatch(r"summary:[0-9a-f]{64}", summary_id) is None:
            raise ExplanationVerificationError("invalid_summary_id")
        cls.validate(result)
        return cls._canonical(
            {"format_version": 1, "summary_id": summary_id, "verification": asdict(result)}
        ).encode("utf-8")
