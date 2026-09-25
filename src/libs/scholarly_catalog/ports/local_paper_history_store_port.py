from typing import Protocol

from libs.scholarly_catalog.dtos.local_paper_record import LocalPaperRecord


class LocalPaperHistoryStorePort(Protocol):
    def read(self, work_id: str) -> LocalPaperRecord: ...
