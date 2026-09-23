from typing import Protocol

from libs.paper_explanations.dtos.current_summary_pointer import CurrentSummaryPointer


class CurrentSummaryStorePort(Protocol):
    def publish(
        self,
        summary_id: str,
        expected_input_fingerprint: str,
        expected_pointer_version: int | None,
    ) -> CurrentSummaryPointer: ...
