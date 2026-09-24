from dataclasses import dataclass


@dataclass(frozen=True)
class CrossrefAttachment:
    claim_id: str
    page_id: str
    receipt_id: str
    attempt_id: str
    action: str
    failure_code: str | None
    replayed: bool
