from inspect import isabstract

from libs.research_workflow.application.queries.read_runtime_version import ReadRuntimeVersion
from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort
from libs.research_workflow.ports.runtime_version_provider_port import RuntimeVersionProviderPort


def test_ports_are_explicit_abstract_boundaries() -> None:
    assert isabstract(ReadRuntimeVersionPort)
    assert isabstract(RuntimeVersionProviderPort)
    assert issubclass(ReadRuntimeVersion, ReadRuntimeVersionPort)
    assert not isabstract(ReadRuntimeVersion)
