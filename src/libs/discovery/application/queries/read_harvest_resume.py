from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.harvest_resume_state import HarvestResumeState
from libs.discovery.ports.harvest_resume_store_port import HarvestResumeStorePort


class ReadHarvestResume:
    def __init__(self, store: HarvestResumeStorePort) -> None:
        self._store = store

    def __call__(self, plan: CompiledSourceQuery, parser_version: str) -> HarvestResumeState:
        return self._store.resume(plan, parser_version)
