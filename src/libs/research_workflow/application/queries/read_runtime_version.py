from injector import inject

from libs.research_workflow.dtos.runtime_version import RuntimeVersion
from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort
from libs.research_workflow.ports.runtime_version_provider_port import RuntimeVersionProviderPort


class ReadRuntimeVersion(ReadRuntimeVersionPort):
    @inject
    def __init__(self, provider: RuntimeVersionProviderPort) -> None:
        self._provider = provider

    def __call__(self) -> RuntimeVersion:
        return self._provider.read()
