class StorageError(RuntimeError):
    """A storage failure with a stable diagnostic code; never an empty-success result."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)
