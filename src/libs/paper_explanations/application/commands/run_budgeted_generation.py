from libs.paper_explanations.domain.services.generation_execution_rules import GenerationExecutionRules
from libs.paper_explanations.dtos.budgeted_generation_execution import (
    BudgetedGenerationExecution,
)
from libs.paper_explanations.dtos.generation_budget_policy import GenerationBudgetPolicy
from libs.paper_explanations.dtos.structured_generation_request import StructuredGenerationRequest
from libs.paper_explanations.dtos.structured_generation_result import StructuredGenerationResult
from libs.paper_explanations.exceptions.generation_ledger_error import GenerationLedgerError
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.ports.generation_clock_port import GenerationClockPort
from libs.paper_explanations.ports.generation_ledger_port import GenerationLedgerPort
from libs.paper_explanations.ports.structured_generation_port import StructuredGenerationPort


class RunBudgetedGeneration:
    """Wrap one structured provider call with durable cache, reservation and reconciliation state."""

    def __init__(
        self,
        model: StructuredGenerationPort,
        ledger: GenerationLedgerPort,
        clock: GenerationClockPort,
        budget: GenerationBudgetPolicy,
    ) -> None:
        self._model = model
        self._ledger = ledger
        self._clock = clock
        self._budget = budget

    def execute(
        self,
        request: StructuredGenerationRequest,
    ) -> BudgetedGenerationExecution:
        identity = GenerationExecutionRules.identity(request, self._budget)
        reservation = self._ledger.reserve(identity, self._clock())
        if reservation.state == "cached":
            if reservation.output_object_id is None:
                raise GenerationLedgerError("generation_ledger_corrupt")
            if reservation.run_id is None:
                raise GenerationLedgerError("generation_ledger_corrupt")
            return BudgetedGenerationExecution(
                reservation.run_id,
                identity.generation_fingerprint,
                self._ledger.read_cached(reservation.output_object_id, identity),
                True,
            )
        if reservation.state == "budget_blocked":
            raise ModelGatewayError("budget_blocked")
        if reservation.state == "in_progress":
            raise GenerationLedgerError("generation_in_progress")
        if reservation.state != "reserved" or reservation.run_id is None:
            raise GenerationLedgerError("generation_ledger_corrupt")

        run_id = reservation.run_id
        self._ledger.mark_running(run_id, identity)
        try:
            result = self._model(request)
        except ModelGatewayError as exc:
            self._ledger.complete_failure(
                run_id,
                identity,
                exc.code,
                exc.receipt,
                no_charge=GenerationExecutionRules.is_known_no_charge(exc.code),
                finished_at=self._clock(),
            )
            raise
        except Exception:
            self._ledger.complete_failure(
                run_id,
                identity,
                "unexpected_model_error",
                None,
                no_charge=False,
                finished_at=self._clock(),
            )
            raise
        if not isinstance(result, StructuredGenerationResult):
            self._ledger.complete_failure(
                run_id,
                identity,
                "invalid_generation_result",
                None,
                no_charge=False,
                finished_at=self._clock(),
            )
            raise GenerationLedgerError("invalid_generation_result")
        self._ledger.complete_success(run_id, identity, result, self._clock())
        return BudgetedGenerationExecution(
            run_id,
            identity.generation_fingerprint,
            result,
            False,
        )

    def __call__(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        return self.execute(request).result
