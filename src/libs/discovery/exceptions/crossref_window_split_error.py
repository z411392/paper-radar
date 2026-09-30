class CrossrefWindowSplitError(RuntimeError):
    """Stable diagnostics that do not expose queries, cursors or contact details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
