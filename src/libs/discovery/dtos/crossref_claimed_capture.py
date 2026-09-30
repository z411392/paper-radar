from dataclasses import dataclass

from libs.discovery.dtos.crossref_capture import CrossrefStoredCapture
from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision


@dataclass(frozen=True)
class CrossrefClaimedCapture:
    stored: CrossrefStoredCapture
    decision: CrossrefRateDecision
