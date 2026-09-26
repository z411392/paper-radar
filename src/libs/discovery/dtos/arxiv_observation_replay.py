from dataclasses import dataclass
from datetime import datetime

from libs.discovery.dtos.arxiv_source_record import ArxivSourceRecord


@dataclass(frozen=True)
class ArxivObservationReplay:
    observation_id: str
    unit_id: str
    payload_object_id: str
    parser_version: str
    observed_at: datetime
    record: ArxivSourceRecord
