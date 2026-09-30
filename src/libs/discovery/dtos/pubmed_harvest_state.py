from dataclasses import dataclass


@dataclass(frozen=True)
class PubmedHarvestState:
    unit_id: str
    checkpoint_version: int
    next_start: int
    total_results: int | None
    state: str
    page_start: int | None
    page_pmids: tuple[str, ...]
    next_batch_offset: int


@dataclass(frozen=True)
class PendingPubmedBatch:
    unit_id: str
    page_start: int
    batch_offset: int
    pmids: tuple[str, ...]
