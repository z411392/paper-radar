from typing import Protocol

from libs.scholarly_catalog.dtos.crossref_integrity_event_source import (
    CrossrefIntegrityEventSource,
)


class ReadCrossrefIntegrityEventSourcePort(Protocol):
    def __call__(
        self,
        assertion_id: str,
    ) -> CrossrefIntegrityEventSource | None: ...
