from datetime import datetime
from typing import Protocol

from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityAssertionRef,
)


class BindCrossrefIntegrityWorksPort(Protocol):
    def __call__(
        self,
        assertions: tuple[CrossrefIntegrityAssertionRef, ...],
        *,
        observed_at: datetime,
    ) -> None: ...
