from dataclasses import dataclass

from libs.scholarly_catalog.dtos.evidence_anchor import EvidenceAnchor


@dataclass(frozen=True)
class ClaimExtractionRequest:
    snapshot_id: str
    revision_id: str
    work_id: str
    evidence_fingerprint: str
    evidence_level: str
    source_text: str
    anchors: tuple[EvidenceAnchor, ...]
    model_name: str
    prompt_version: str
    schema_version: str
    system_prompt: str
    response_schema_json: str
    payload_json: str
    input_fingerprint: str
