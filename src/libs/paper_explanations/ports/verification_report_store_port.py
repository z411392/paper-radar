from typing import Protocol


class VerificationReportStorePort(Protocol):
    def publish(self, content: bytes) -> str: ...
