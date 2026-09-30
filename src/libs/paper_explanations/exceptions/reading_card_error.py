from libs.paper_explanations.dtos.structured_generation_result import GenerationReceipt


class ReadingCardError(ValueError):
    def __init__(self, code: str, receipt: GenerationReceipt | None = None) -> None:
        self.code = code
        self.receipt = receipt
        super().__init__(code)
