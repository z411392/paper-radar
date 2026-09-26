from contextlib import AbstractContextManager
from typing import Protocol

from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision


class CrossrefRateLeasePort(Protocol):
    def observe(
        self,
        status: int | None,
        headers: tuple[tuple[str, str], ...],
        *,
        capture_error: str | None = None,
    ) -> CrossrefRateDecision: ...


class CrossrefRateGatePort(Protocol):
    def slot(self, contact_email: str) -> AbstractContextManager[CrossrefRateLeasePort]: ...

    def observe_received(
        self,
        contact_email: str,
        status: int | None,
        headers: tuple[tuple[str, str], ...],
        *,
        capture_error: str | None = None,
    ) -> CrossrefRateDecision: ...
