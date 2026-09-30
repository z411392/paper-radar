from typing import Protocol

from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.research_workflow.dtos.harvest_query_request import HarvestQueryRequest


class BuildHarvestQueryInputPort(Protocol):
    def __call__(self, request: HarvestQueryRequest) -> SourceQueryInput: ...
