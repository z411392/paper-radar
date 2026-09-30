class SourceParseError(Exception):
    def __init__(self, code: str, response_sha256: str | None = None) -> None:
        self.code = code
        self.response_sha256 = response_sha256
        self.parser_version = "arxiv-atom-v1"
        super().__init__(code)
