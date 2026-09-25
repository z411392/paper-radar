from libs.paper_explanations.dtos.structured_generation_result import GenerationReceipt


class ModelGatewayError(RuntimeError):
    """Safe diagnostic only; never attach a provider response body or credentials."""

    def __init__(
        self,
        code: str,
        receipt: GenerationReceipt | None = None,
        *,
        run_id: str | None = None,
        generation_fingerprint: str | None = None,
    ) -> None:
        self.code = code
        self.receipt = receipt
        self.run_id = run_id
        self.generation_fingerprint = generation_fingerprint
        super().__init__(code)
