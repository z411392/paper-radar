from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SourceHttpResponse:
    """Captured bytes, not parsed or durable evidence. Never print the body."""

    status: int
    body: bytes = field(repr=False)
    headers: tuple[tuple[str, str], ...]
    received_at: datetime
    capture_error: str | None = None

    @property
    def body_complete(self) -> bool:
        return self.capture_error is None
