class SourceFetchError(Exception):
    def __init__(
        self, code: str, *, retryable: bool = False, retry_after_seconds: float | None = None
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)
