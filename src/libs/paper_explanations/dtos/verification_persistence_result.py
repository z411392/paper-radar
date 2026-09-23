from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationPersistenceResult:
    summary_id: str
    qa_state: str
    report_object_id: str
    deterministic_verification_id: str
    support_verification_id: str | None
    verified_at: str
