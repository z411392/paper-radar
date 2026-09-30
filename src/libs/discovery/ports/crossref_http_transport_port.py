from typing import Protocol

from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan


class CrossrefHttpTransportPort(Protocol):
    def get(self, plan: CrossrefWindowPlan, request: CrossrefPageRequest) -> CrossrefHttpCapture: ...
