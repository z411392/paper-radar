import hashlib
import json
import re
from dataclasses import asdict

from libs.paper_explanations.dtos.claim_candidate import ClaimCandidate
from libs.paper_explanations.dtos.claim_extraction_request import ClaimExtractionRequest
from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult, PaperClaim
from libs.paper_explanations.exceptions.claim_extraction_error import ClaimExtractionError
from libs.paper_explanations.prompts.claim_extraction_prompt import (
    CLAIM_TYPES,
    GAP_TOPICS,
    MODEL_NAME,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    response_schema,
)
from libs.scholarly_catalog.dtos.evidence_anchor import EvidenceAnchor
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot
from libs.scholarly_catalog.dtos.evidence_snapshot_readback import EvidenceSnapshotReadback


class ClaimExtractionRules:
    @staticmethod
    def _json(value: object, code: str) -> str:
        try:
            text = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            text.encode("utf-8")
            return text
        except (ValueError, TypeError, RecursionError) as exc:
            raise ClaimExtractionError(code) from exc

    @staticmethod
    def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ClaimExtractionError("invalid_claim_response")
            result[key] = value
        return result

    @staticmethod
    def _constant(value: str) -> None:
        raise ClaimExtractionError("invalid_claim_response")

    @classmethod
    def _load(cls, value: str) -> dict:
        try:
            if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 65536:
                raise ClaimExtractionError("invalid_claim_response")
            decoded = json.loads(value, object_pairs_hook=cls._unique, parse_constant=cls._constant)
            cls._json(decoded, "invalid_claim_response")
        except (ValueError, TypeError, RecursionError) as exc:
            raise ClaimExtractionError("invalid_claim_response") from exc
        if not isinstance(decoded, dict):
            raise ClaimExtractionError("invalid_claim_response")
        return decoded

    @staticmethod
    def snapshot_id(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"snapshot:[0-9a-f]{64}", value) is None:
            raise ClaimExtractionError("invalid_claim_evidence")
        return value

    @classmethod
    def request(cls, snapshot_id: str, evidence: EvidenceSnapshotReadback) -> ClaimExtractionRequest:
        cls.snapshot_id(snapshot_id)
        if not isinstance(evidence, EvidenceSnapshotReadback) or not isinstance(
            evidence.snapshot, EvidenceSnapshot
        ):
            raise ClaimExtractionError("invalid_claim_evidence")
        snapshot = evidence.snapshot
        text = evidence.normalized_text
        if (
            snapshot.snapshot_id != snapshot_id
            or snapshot.snapshot_id != f"snapshot:{snapshot.fingerprint}"
            or not isinstance(snapshot.evidence_level, str)
            or snapshot.evidence_level not in {"abstract_only", "selected_sections", "full_text"}
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(evidence.source_bytes, bytes)
            or not evidence.source_bytes
        ):
            raise ClaimExtractionError("invalid_claim_evidence")
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ClaimExtractionError("invalid_claim_evidence") from exc
        if len(encoded) > 262144:
            raise ClaimExtractionError("claim_evidence_too_large")
        source_kind = "evidence" if snapshot.evidence_level == "abstract_only" else "fulltext"
        if (
            snapshot.text_object_id != "extracted:" + hashlib.sha256(encoded).hexdigest()
            or snapshot.object_id != source_kind + ":" + hashlib.sha256(evidence.source_bytes).hexdigest()
        ):
            raise ClaimExtractionError("invalid_claim_evidence")
        for value, prefix in ((snapshot.revision_id, "revision"), (snapshot.work_id, "work")):
            if not isinstance(value, str) or re.fullmatch(prefix + r":[0-9a-f]{64}", value) is None:
                raise ClaimExtractionError("invalid_claim_evidence")
        if not isinstance(snapshot.anchors, tuple) or not 1 <= len(snapshot.anchors) <= 128:
            raise ClaimExtractionError("invalid_claim_evidence")
        seen: set[str] = set()
        for anchor in snapshot.anchors:
            if (
                not isinstance(anchor, EvidenceAnchor)
                or not isinstance(anchor.anchor_id, str)
                or re.fullmatch(r"anchor:[0-9a-f]{64}", anchor.anchor_id) is None
                or anchor.anchor_id in seen
                or anchor.snapshot_id != snapshot_id
                or type(anchor.offset_start) is not int
                or type(anchor.offset_end) is not int
                or not 0 <= anchor.offset_start < anchor.offset_end <= len(text)
                or not isinstance(anchor.quote, str)
                or text[anchor.offset_start:anchor.offset_end] != anchor.quote
            ):
                raise ClaimExtractionError("invalid_claim_evidence")
            seen.add(anchor.anchor_id)
        anchors = tuple(sorted(snapshot.anchors, key=lambda a: (a.offset_start, a.offset_end, a.anchor_id)))
        payload = {
            "snapshot_id": snapshot_id, "revision_id": snapshot.revision_id, "work_id": snapshot.work_id,
            "evidence_fingerprint": snapshot.fingerprint, "evidence_level": snapshot.evidence_level,
            "source_text": text, "anchors": [asdict(anchor) for anchor in anchors],
        }
        payload_json = cls._json(payload, "invalid_claim_evidence")
        schema = response_schema()
        identity = {
            "model_name": MODEL_NAME, "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
            "system_prompt": SYSTEM_PROMPT, "schema": json.loads(schema), "payload": payload,
        }
        fingerprint = hashlib.sha256(cls._json(identity, "invalid_claim_evidence").encode()).hexdigest()
        return ClaimExtractionRequest(
            snapshot_id, snapshot.revision_id, snapshot.work_id, snapshot.fingerprint,
            snapshot.evidence_level, text, anchors, MODEL_NAME, PROMPT_VERSION, SCHEMA_VERSION,
            SYSTEM_PROMPT, schema, payload_json, fingerprint,
        )

    @classmethod
    def parse(cls, request: ClaimExtractionRequest, candidate: ClaimCandidate) -> ClaimExtractionResult:
        if not isinstance(candidate, ClaimCandidate):
            raise ClaimExtractionError("invalid_claim_response")
        if candidate.model_name != request.model_name:
            raise ClaimExtractionError("claim_model_mismatch")
        if candidate.finish_reason != "stop":
            raise ClaimExtractionError("incomplete_claim_response")
        if (
            not isinstance(candidate.run_id, str)
            or re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", candidate.run_id) is None
        ):
            raise ClaimExtractionError("invalid_claim_response")
        data = cls._load(candidate.content_json)
        if set(data) != {
            "schema_version", "snapshot_id", "input_fingerprint", "claims", "not_reported_in_read_evidence"
        }:
            raise ClaimExtractionError("invalid_claim_response")
        if data["schema_version"] != request.schema_version:
            raise ClaimExtractionError("invalid_claim_response")
        if data["snapshot_id"] != request.snapshot_id:
            raise ClaimExtractionError("claim_snapshot_mismatch")
        if data["input_fingerprint"] != request.input_fingerprint:
            raise ClaimExtractionError("claim_input_mismatch")
        raw_claims, gaps = data["claims"], data["not_reported_in_read_evidence"]
        if (
            not isinstance(raw_claims, list) or len(raw_claims) > 64
            or not isinstance(gaps, list) or len(gaps) > len(GAP_TOPICS)
            or any(not isinstance(gap, str) or gap not in GAP_TOPICS for gap in gaps)
            or len(gaps) != len(set(gaps))
        ):
            raise ClaimExtractionError("invalid_claim_response")
        anchors = {anchor.anchor_id: anchor for anchor in request.anchors}
        claims: list[PaperClaim] = []
        seen: set[str] = set()
        for raw in raw_claims:
            if not isinstance(raw, dict) or set(raw) != {"claim_type", "anchor_ids"}:
                raise ClaimExtractionError("invalid_claim_response")
            kind, ids = raw["claim_type"], raw["anchor_ids"]
            if (
                not isinstance(kind, str) or kind not in CLAIM_TYPES
                or not isinstance(ids, list) or not 1 <= len(ids) <= 8
                or any(not isinstance(aid, str) or aid not in anchors for aid in ids)
                or len(ids) != len(set(ids))
            ):
                raise ClaimExtractionError("invalid_claim_response")
            ordered = tuple(sorted(ids))
            payload = {
                "input_fingerprint": request.input_fingerprint, "claim_type": kind, "anchor_ids": ordered
            }
            cid = "claim:" + hashlib.sha256(cls._json(payload, "invalid_claim_response").encode()).hexdigest()
            if cid in seen:
                raise ClaimExtractionError("invalid_claim_response")
            seen.add(cid)
            claims.append(PaperClaim(cid, kind, ordered, tuple(anchors[aid].quote for aid in ordered)))
        return ClaimExtractionResult(request, candidate.run_id, tuple(claims), tuple(sorted(gaps)))
