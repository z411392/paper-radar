class CrossrefCaptureError(RuntimeError):
    def __init__(self, code: str, receipt_id: str | None = None) -> None:
        self.code = code
        self.receipt_id = receipt_id
        super().__init__(code)
