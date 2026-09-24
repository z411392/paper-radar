from typing import Protocol

from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.source_query_input import SourceQueryInput


class BuildCrossrefWindowPlanPort(Protocol):
    def __call__(
        self,
        query: SourceQueryInput,
        *,
        binding_key: str,
    ) -> CrossrefWindowPlan: ...
