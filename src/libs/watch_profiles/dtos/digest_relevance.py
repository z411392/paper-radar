from dataclasses import dataclass


@dataclass(frozen=True)
class DigestRelevance:
    domain_id: str
    decision: str
