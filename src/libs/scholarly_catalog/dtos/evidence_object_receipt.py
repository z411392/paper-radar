from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceObjectReceipt:
    object_id: str
    content_sha256: str
