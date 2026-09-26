from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryHealthEvidence:
    unknown_deliveries: int
