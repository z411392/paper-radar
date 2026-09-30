from typing import Protocol

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.harvest_resume_state import HarvestResumeState


class HarvestResumeStorePort(Protocol):
    def resume(self, plan: CompiledSourceQuery, parser_version: str) -> HarvestResumeState: ...
