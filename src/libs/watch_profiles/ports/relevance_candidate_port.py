from typing import Protocol

from libs.watch_profiles.dtos.relevance_assessment import RelevanceCandidate, RelevanceRequest


class RelevanceCandidatePort(Protocol):
    def __call__(self, request: RelevanceRequest) -> RelevanceCandidate: ...
