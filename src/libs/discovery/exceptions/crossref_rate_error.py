class CrossrefRateError(RuntimeError):
    """Stable diagnostics without the contact address, cursor or filesystem path."""

    def __init__(self, code: str, retry_after_seconds: float | None = None) -> None:
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)
