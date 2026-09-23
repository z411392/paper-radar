from datetime import datetime
from typing import Protocol

from libs.paper_explanations.dtos.generation_identity import GenerationIdentity
from libs.paper_explanations.dtos.generation_reservation import GenerationReservation
from libs.paper_explanations.dtos.structured_generation_result import GenerationReceipt, StructuredGenerationResult


class GenerationLedgerPort(Protocol):
    def reserve(self, identity: GenerationIdentity, started_at: datetime) -> GenerationReservation: ...

    def read_cached(
        self, output_object_id: str, identity: GenerationIdentity
    ) -> StructuredGenerationResult: ...

    def mark_running(self, run_id: str, identity: GenerationIdentity) -> None: ...

    def complete_success(
        self,
        run_id: str,
        identity: GenerationIdentity,
        result: StructuredGenerationResult,
        finished_at: datetime,
    ) -> None: ...

    def complete_failure(
        self,
        run_id: str,
        identity: GenerationIdentity,
        error_code: str,
        receipt: GenerationReceipt | None,
        *,
        no_charge: bool,
        finished_at: datetime,
    ) -> None: ...

    def reconcile_stale(self, before: datetime, reconciled_at: datetime) -> tuple[int, int]: ...
