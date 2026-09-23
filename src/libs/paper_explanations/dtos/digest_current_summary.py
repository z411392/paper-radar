from dataclasses import dataclass


@dataclass(frozen=True)
class DigestCurrentSummary:
    work_id: str
    revision_id: str
    summary_id: str
    plain_language: tuple[str, ...]
