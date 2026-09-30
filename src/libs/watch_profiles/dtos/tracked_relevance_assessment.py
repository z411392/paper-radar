from dataclasses import dataclass

from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment


@dataclass(frozen=True)
class TrackedRelevanceAssessment:
    assessment: RelevanceAssessment
    run_id: str | None
    generation_fingerprint: str | None
