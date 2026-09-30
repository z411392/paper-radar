import platform
from importlib.metadata import version

from libs.research_workflow.dtos.runtime_version import RuntimeVersion
from libs.research_workflow.ports.runtime_version_provider_port import RuntimeVersionProviderPort


class PythonRuntimeVersionAdapter(RuntimeVersionProviderPort):
    def read(self) -> RuntimeVersion:
        return RuntimeVersion("paper-radar", version("paper-radar"), platform.python_version())
