from dataclasses import dataclass


@dataclass(frozen=True)
class GenerationReservation:
    state: str
    run_id: str | None = None
    output_object_id: str | None = None
