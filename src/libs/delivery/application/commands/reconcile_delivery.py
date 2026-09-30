from libs.delivery.dtos.delivery_dispatch import ReconciliationOutcome
from libs.delivery.ports.delivery_dispatch_store_port import DeliveryDispatchStorePort


class ReconcileDelivery:
    def __init__(self, store: DeliveryDispatchStorePort) -> None:
        self._store = store

    def __call__(self, outbox_id: str) -> ReconciliationOutcome:
        candidate = self._store.load_dispatch(outbox_id)
        if candidate.outbox_state == "unknown":
            return ReconciliationOutcome(
                "manual_action_required",
                "delivery_unknown_no_provider_lookup",
            )
        if candidate.outbox_state == "sending":
            return ReconciliationOutcome(
                "manual_action_required",
                "inflight_delivery_without_terminal_receipt",
            )
        if candidate.outbox_state == "failed":
            return ReconciliationOutcome("manual_action_required", "delivery_rejected")
        return ReconciliationOutcome(candidate.outbox_state, None)
