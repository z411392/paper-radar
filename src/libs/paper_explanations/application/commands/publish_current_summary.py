from libs.paper_explanations.dtos.current_summary_pointer import CurrentSummaryPointer
from libs.paper_explanations.ports.current_summary_store_port import CurrentSummaryStorePort


class PublishCurrentSummary:
    def __init__(self, store: CurrentSummaryStorePort) -> None:
        self._store = store

    def __call__(
        self,
        summary_id: str,
        *,
        expected_input_fingerprint: str,
        expected_pointer_version: int | None,
    ) -> CurrentSummaryPointer:
        return self._store.publish(summary_id, expected_input_fingerprint, expected_pointer_version)
