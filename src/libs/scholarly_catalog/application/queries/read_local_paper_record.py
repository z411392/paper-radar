from libs.scholarly_catalog.dtos.local_paper_record import LocalPaperRecord
from libs.scholarly_catalog.ports.local_paper_history_store_port import (
    LocalPaperHistoryStorePort,
)


class ReadLocalPaperRecord:
    def __init__(self, store: LocalPaperHistoryStorePort) -> None:
        self._store = store

    def __call__(self, work_id: str) -> LocalPaperRecord:
        return self._store.read(work_id)
