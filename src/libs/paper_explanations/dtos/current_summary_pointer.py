from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentSummaryPointer:
    work_id: str
    language: str
    explanation_profile: str
    summary_id: str
    revision_id: str
    expected_input_fingerprint: str
    pointer_version: int
