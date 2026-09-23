import hashlib
import json
import re
from dataclasses import asdict

from libs.paper_explanations.dtos.claim_extraction_request import ClaimExtractionRequest
from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult
from libs.scholarly_catalog.dtos.evidence_anchor import EvidenceAnchor
from libs.watch_profiles.dtos.domain_definition import DomainDefinition
from libs.watch_profiles.dtos.profile_revision import ProfileRevision
from libs.watch_profiles.dtos.relevance_assessment import (
    RelevanceAssessment,
    RelevanceCandidate,
    RelevanceRequest,
)
from libs.watch_profiles.exceptions.relevance_assessment_error import RelevanceAssessmentError
from libs.watch_profiles.prompts.relevance_prompt import (
    MODEL_NAME,
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    response_schema,
)


class RelevanceRules:
    @staticmethod
    def _json(value: object, code: str) -> str:
        try:
            result = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            result.encode("utf-8")
            return result
        except (ValueError, TypeError, RecursionError) as exc:
            raise RelevanceAssessmentError(code) from exc

    @staticmethod
    def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise RelevanceAssessmentError("invalid_relevance_response")
            result[key] = value
        return result

    @staticmethod
    def _constant(value: str) -> None:
        raise RelevanceAssessmentError("invalid_relevance_response")

    @classmethod
    def _load(cls, value: str, code: str) -> dict:
        try:
            if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 65536:
                raise RelevanceAssessmentError(code)
            result = json.loads(value, object_pairs_hook=cls._unique, parse_constant=cls._constant)
            cls._json(result, code)
        except (ValueError, TypeError, RecursionError) as exc:
            raise RelevanceAssessmentError(code) from exc
        if not isinstance(result, dict):
            raise RelevanceAssessmentError(code)
        return result

    @staticmethod
    def _id(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
            raise RelevanceAssessmentError("invalid_relevance_input")
        return value

    @classmethod
    def profile(cls, profile_id: str, domain_id: str, profile: ProfileRevision) -> int:
        cls._id(profile_id)
        cls._id(domain_id)
        if (
            not isinstance(profile, ProfileRevision)
            or profile.profile_id != profile_id
            or type(profile.revision) is not int
            or not 1 <= profile.revision < 2**63
            or type(profile.current_revision) is not int
            or not isinstance(profile.fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", profile.fingerprint) is None
            or not isinstance(profile.scope_text, str)
            or not profile.scope_text.strip()
        ):
            raise RelevanceAssessmentError("invalid_relevance_input")
        if profile.lifecycle != "active" or profile.revision != profile.current_revision:
            raise RelevanceAssessmentError("profile_not_current")
        cls._load(profile.filters_json, "invalid_relevance_input")
        if not isinstance(profile.domains, tuple):
            raise RelevanceAssessmentError("invalid_relevance_input")
        selected: dict[str, int] = {}
        for item in profile.domains:
            if not isinstance(item, tuple) or len(item) != 2:
                raise RelevanceAssessmentError("invalid_relevance_input")
            name, revision = item
            cls._id(name)
            if type(revision) is not int or not 1 <= revision < 2**63 or name in selected:
                raise RelevanceAssessmentError("invalid_relevance_input")
            selected[name] = revision
        if domain_id not in selected:
            raise RelevanceAssessmentError("domain_not_selected")
        return selected[domain_id]

    @classmethod
    def request(
        cls, profile: ProfileRevision, domain: DomainDefinition, claims: ClaimExtractionResult
    ) -> RelevanceRequest:
        if not isinstance(domain, DomainDefinition):
            raise RelevanceAssessmentError("invalid_relevance_input")
        expected_revision = cls.profile(profile.profile_id, domain.domain_id, profile)
        if type(domain.revision) is not int or domain.revision != expected_revision:
            raise RelevanceAssessmentError("invalid_relevance_input")
        if not isinstance(claims, ClaimExtractionResult) or not isinstance(
            claims.request, ClaimExtractionRequest
        ):
            raise RelevanceAssessmentError("invalid_relevance_input")
        evidence = claims.request
        if (
            claims.validation_state != "anchor_bound_candidate"
            or not isinstance(evidence.source_text, str)
            or not isinstance(evidence.anchors, tuple)
            or not 1 <= len(evidence.anchors) <= 128
        ):
            raise RelevanceAssessmentError("invalid_relevance_input")
        seen: set[str] = set()
        for anchor in evidence.anchors:
            if (
                not isinstance(anchor, EvidenceAnchor)
                or not isinstance(anchor.anchor_id, str)
                or anchor.anchor_id in seen
                or anchor.snapshot_id != evidence.snapshot_id
                or type(anchor.offset_start) is not int
                or type(anchor.offset_end) is not int
                or not 0 <= anchor.offset_start < anchor.offset_end <= len(evidence.source_text)
                or evidence.source_text[anchor.offset_start:anchor.offset_end] != anchor.quote
            ):
                raise RelevanceAssessmentError("invalid_relevance_input")
            seen.add(anchor.anchor_id)
        payload = {
            "profile": {"profile_id": profile.profile_id, "revision": profile.revision,
                        "fingerprint": profile.fingerprint, "scope_text": profile.scope_text,
                        "filters": cls._load(profile.filters_json, "invalid_relevance_input")},
            "domain": asdict(domain),
            "evidence": {"snapshot_id": evidence.snapshot_id, "revision_id": evidence.revision_id,
                         "evidence_fingerprint": evidence.evidence_fingerprint,
                         "evidence_level": evidence.evidence_level, "source_text": evidence.source_text,
                         "anchors": [asdict(anchor) for anchor in evidence.anchors]},
        }
        payload_json = cls._json(payload, "invalid_relevance_input")
        if len(payload_json.encode()) > 524288:
            raise RelevanceAssessmentError("relevance_input_too_large")
        schema = response_schema()
        fingerprint = hashlib.sha256(cls._json({
            "model_name": MODEL_NAME, "schema_version": SCHEMA_VERSION,
            "system_prompt": SYSTEM_PROMPT, "schema": json.loads(schema), "payload": payload,
        }, "invalid_relevance_input").encode()).hexdigest()
        return RelevanceRequest(profile.profile_id, profile.revision, profile.fingerprint,
                                domain.domain_id, domain.revision, evidence.snapshot_id,
                                MODEL_NAME, SCHEMA_VERSION, SYSTEM_PROMPT, payload_json, schema, fingerprint)

    @classmethod
    def parse(
        cls, request: RelevanceRequest, candidate: RelevanceCandidate, claims: ClaimExtractionResult
    ) -> RelevanceAssessment:
        if not isinstance(candidate, RelevanceCandidate):
            raise RelevanceAssessmentError("invalid_relevance_response")
        if candidate.model_name != request.model_name:
            raise RelevanceAssessmentError("relevance_model_mismatch")
        if candidate.finish_reason != "stop":
            raise RelevanceAssessmentError("incomplete_relevance_response")
        data = cls._load(candidate.content_json, "invalid_relevance_response")
        if set(data) != {"schema_version", "snapshot_id", "profile_id", "domain_id", "input_fingerprint",
                         "profile_revision", "domain_revision",
                         "decision", "recommendation_reason", "anchor_ids"}:
            raise RelevanceAssessmentError("invalid_relevance_response")
        if (
            data["profile_id"] != request.profile_id or data["domain_id"] != request.domain_id
            or data["input_fingerprint"] != request.input_fingerprint
            or data["schema_version"] != request.schema_version
            or data["snapshot_id"] != request.snapshot_id
            or type(data["profile_revision"]) is not int
            or data["profile_revision"] != request.profile_revision
            or type(data["domain_revision"]) is not int or data["domain_revision"] != request.domain_revision
        ):
            raise RelevanceAssessmentError("relevance_version_mismatch")
        decision, reason, ids = data["decision"], data["recommendation_reason"], data["anchor_ids"]
        anchors = {a.anchor_id: a for a in claims.request.anchors}
        if (
            not isinstance(decision, str) or decision not in {"direct", "adjacent", "uncertain", "irrelevant"}
            or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 4096 or "\x00" in reason
            or not isinstance(ids, list) or len(ids) > 16
            or any(not isinstance(aid, str) or aid not in anchors for aid in ids)
            or len(ids) != len(set(ids))
            or decision in {"direct", "adjacent"} and not ids
        ):
            raise RelevanceAssessmentError("invalid_relevance_response")
        if request.domain_id == "badminton" and decision == "direct":
            # Necessary lexical evidence only, not proof of semantic relevance.
            if not any(re.search(r"\bbadminton\b|羽球|羽毛球", anchors[aid].quote, re.IGNORECASE) for aid in ids):
                raise RelevanceAssessmentError("direct_evidence_missing")
        return RelevanceAssessment(request.input_fingerprint, request.profile_id, request.profile_revision,
                                   request.domain_id, request.domain_revision, request.snapshot_id,
                                   "succeeded", decision, reason, tuple(sorted(ids)), None)

    @staticmethod
    def failure(request: RelevanceRequest, state: str, code: str) -> RelevanceAssessment:
        if state not in {"failed", "stale"}:
            raise RelevanceAssessmentError("invalid_relevance_state")
        return RelevanceAssessment(request.input_fingerprint, request.profile_id, request.profile_revision,
                                   request.domain_id, request.domain_revision, request.snapshot_id,
                                   state, None, None, (), code)
