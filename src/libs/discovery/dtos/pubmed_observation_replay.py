from dataclasses import dataclass
from datetime import datetime

from libs.discovery.dtos.pubmed_bibliography_record import PubmedBibliographyRecord


@dataclass(frozen=True)
class PubmedObservationReplay:
    observation_id: str
    unit_id: str
    payload_object_id: str
    parser_version: str
    observed_at: datetime
    record: PubmedBibliographyRecord
