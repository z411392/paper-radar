from typing import Protocol

from libs.scholarly_catalog.dtos.local_paper_record import LocalPaperRecord


class ReadLocalPaperRecordPort(Protocol):
    def __call__(self, work_id: str) -> LocalPaperRecord: ...
