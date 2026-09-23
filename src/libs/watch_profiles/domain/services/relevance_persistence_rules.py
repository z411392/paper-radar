import hashlib
import json
import re

from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment
from libs.watch_profiles.dtos.relevance_persistence import PreparedRelevanceAssessment
from libs.watch_profiles.exceptions.relevance_persistence_error import RelevancePersistenceError


class RelevancePersistenceRules:
    @staticmethod
    def _identifier(value: object, code: str) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
            raise RelevancePersistenceError(code)
        return value

    @staticmethod
    def _positive(value: object, code: str) -> int:
        if type(value) is not int or not 1 <= value < 2**63:
            raise RelevancePersistenceError(code)
        return value

    @staticmethod
    def _text(value: object, code: str, *, maximum: int = 4096) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
            raise RelevancePersistenceError(code)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise RelevancePersistenceError(code) from None
        return value

    @staticmethod
    def _fingerprint(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise RelevancePersistenceError("invalid_relevance_fingerprint")
        return value

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
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise RelevancePersistenceError("invalid_relevance_assessment") from exc

    @classmethod
    def prepare(cls, assessment: RelevanceAssessment) -> PreparedRelevanceAssessment:
        if not isinstance(assessment, RelevanceAssessment):
            raise RelevancePersistenceError("invalid_relevance_assessment")
        profile_id = cls._identifier(assessment.profile_id, "invalid_relevance_profile")
        domain_id = cls._identifier(assessment.domain_id, "invalid_relevance_domain")
        profile_revision = cls._positive(
            assessment.profile_revision,
            "invalid_relevance_profile",
        )
        domain_revision = cls._positive(
            assessment.domain_revision,
            "invalid_relevance_domain",
        )
        snapshot_id = cls._text(
            assessment.snapshot_id,
            "invalid_relevance_snapshot",
            maximum=256,
        )
        fingerprint = cls._fingerprint(assessment.input_fingerprint)

        if assessment.execution_state == "succeeded":
            if assessment.decision not in {"direct", "adjacent", "uncertain", "irrelevant"}:
                raise RelevancePersistenceError("invalid_relevance_decision")
            reason = cls._text(
                assessment.recommendation_reason,
                "invalid_relevance_reason",
            )
            if assessment.error_code is not None:
                raise RelevancePersistenceError("invalid_relevance_assessment")
            if (
                not isinstance(assessment.anchor_ids, tuple)
                or len(assessment.anchor_ids) > 16
                or len(assessment.anchor_ids) != len(set(assessment.anchor_ids))
            ):
                raise RelevancePersistenceError("invalid_relevance_anchors")
            anchors = [
                cls._text(value, "invalid_relevance_anchors", maximum=256)
                for value in assessment.anchor_ids
            ]
            if assessment.decision in {"direct", "adjacent"} and not anchors:
                raise RelevancePersistenceError("invalid_relevance_anchors")
            error_code = None
        elif assessment.execution_state in {"failed", "stale"}:
            if (
                assessment.decision is not None
                or assessment.recommendation_reason is not None
                or assessment.anchor_ids != ()
            ):
                raise RelevancePersistenceError("invalid_relevance_assessment")
            reason = None
            anchors = []
            error_code = cls._text(
                assessment.error_code,
                "invalid_relevance_error",
                maximum=256,
            )
        else:
            raise RelevancePersistenceError("invalid_relevance_state")

        identity = cls._canonical(
            {
                "profile_id": profile_id,
                "profile_revision": profile_revision,
                "domain_id": domain_id,
                "domain_revision": domain_revision,
                "snapshot_id": snapshot_id,
                "input_fingerprint": fingerprint,
            }
        )
        assessment_id = "relevance:" + hashlib.sha256(
            ("domain-relevance-v1\0" + identity).encode("utf-8")
        ).hexdigest()
        reason_json = cls._canonical(
            {
                "format_version": 1,
                "domain_id": domain_id,
                "domain_revision": domain_revision,
                "snapshot_id": snapshot_id,
                "recommendation_reason": reason,
                "anchor_ids": anchors,
                "error_code": error_code,
            }
        )
        return PreparedRelevanceAssessment(assessment_id, reason_json)
