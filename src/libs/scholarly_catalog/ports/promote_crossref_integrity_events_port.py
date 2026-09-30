from typing import Protocol

from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityAssertionRef,
)


class PromoteCrossrefIntegrityEventsPort(Protocol):
    def __call__(
        self,
        assertions: tuple[CrossrefIntegrityAssertionRef, ...],
    ) -> tuple[str, ...]: ...
