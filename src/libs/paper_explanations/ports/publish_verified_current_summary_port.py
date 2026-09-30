from typing import Protocol

from libs.paper_explanations.dtos.current_summary_pointer import (
    CurrentSummaryPointer,
)
from libs.paper_explanations.dtos.explanation_persistence import (
    PersistedExplanation,
)


class PublishVerifiedCurrentSummaryPort(Protocol):
    def __call__(
        self,
        explanation: PersistedExplanation,
    ) -> CurrentSummaryPointer: ...
