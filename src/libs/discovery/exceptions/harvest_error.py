class HarvestError(Exception):
    """Stable diagnostics without source bodies or private database paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
