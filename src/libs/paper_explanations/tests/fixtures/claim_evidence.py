import hashlib
import json

from libs.scholarly_catalog.dtos.evidence_anchor import EvidenceAnchor
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot
from libs.scholarly_catalog.dtos.evidence_snapshot_readback import EvidenceSnapshotReadback


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence(text="We study badminton.\nProposed MAE was 0.42 m; baseline MAE was 0.58 m."):
    digest = hashlib.sha256(text.encode()).hexdigest()
    coverage = {"content_scope": "abstract", "document_complete": False,
                "included_sections": ["abstract"], "missing_required_sections": []}
    meta = {"format_version": 1, "revision_id": "revision:" + "1" * 64,
            "work_id": "work:" + "2" * 64, "object_id": "evidence:" + digest,
            "text_object_id": "extracted:" + digest, "parser_version": "fixture-v1",
            "evidence_level": "abstract_only", "coverage": coverage}
    fingerprint = hashlib.sha256(canonical(meta).encode()).hexdigest()
    sid = "snapshot:" + fingerprint
    anchors = []
    offset = 0
    for index, quote in enumerate(text.split("\n")):
        payload = {"snapshot_id": sid, "quote": quote, "offset_start": offset,
                   "offset_end": offset + len(quote), "section_label": "abstract",
                   "paragraph_id": f"p{index}", "table_locator_json": None}
        aid = "anchor:" + hashlib.sha256(canonical(payload).encode()).hexdigest()
        anchors.append(EvidenceAnchor(aid, sid, "abstract", f"p{index}", quote,
                                      offset, offset + len(quote), None))
        offset += len(quote) + 1
    snapshot = EvidenceSnapshot(sid, meta["revision_id"], meta["work_id"], meta["object_id"],
                                meta["text_object_id"], "fixture-v1", "abstract_only",
                                canonical(coverage), fingerprint, "2026-09-23T00:00:00+00:00",
                                tuple(anchors))
    return EvidenceSnapshotReadback(snapshot, text.encode(), text)


def response(request, claim_type="result"):
    return {"schema_version": request.schema_version, "snapshot_id": request.snapshot_id,
            "input_fingerprint": request.input_fingerprint,
            "claims": [{"claim_type": claim_type, "anchor_ids": [request.anchors[-1].anchor_id]}],
            "not_reported_in_read_evidence": ["external_validation"]}
