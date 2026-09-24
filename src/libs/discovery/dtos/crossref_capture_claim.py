from dataclasses import dataclass
from datetime import datetime

from libs.discovery.dtos.crossref_page import CrossrefPageRequest


@dataclass(frozen=True)
class CrossrefCaptureClaim:
    claim_id: str
    page_id: str
    pass_id: str
    fencing_token: int
    owner_id: str
    workspace_id: str
    workspace_epoch: int
    request: CrossrefPageRequest
    reserved_at: datetime
    lease_until: datetime
    state: str
