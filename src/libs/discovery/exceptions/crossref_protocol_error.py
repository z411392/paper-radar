class CrossrefProtocolError(ValueError):
    """Stable diagnostics; never include raw metadata, cursor or contact values."""

    def __init__(self, code: str, response_sha256: str | None = None) -> None:
        self.code = code
        self.response_sha256 = response_sha256
        super().__init__(code)
