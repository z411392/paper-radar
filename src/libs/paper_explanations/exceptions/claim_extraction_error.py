class ClaimExtractionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ClaimModelError(ClaimExtractionError):
    """The provider port maps failures to safe codes, never response bodies."""

    def __init__(self, code: str) -> None:
        if code not in {"model_unavailable", "budget_blocked", "timeout", "transport_error", "refused"}:
            code = "model_unavailable"
        super().__init__(code)
