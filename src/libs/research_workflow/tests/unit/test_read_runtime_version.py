import pytest

from libs.research_workflow.application.queries.read_runtime_version import ReadRuntimeVersion
from libs.research_workflow.dtos.runtime_version import RuntimeVersion
from libs.research_workflow.ports.runtime_version_provider_port import RuntimeVersionProviderPort


class FixedProvider(RuntimeVersionProviderPort):
    def read(self) -> RuntimeVersion:
        return RuntimeVersion("synthetic-package", "2.0.0", "3.13.5")


class FailingProvider(RuntimeVersionProviderPort):
    def read(self) -> RuntimeVersion:
        raise LookupError("synthetic missing package")


def test_query_uses_the_explicit_provider() -> None:
    assert ReadRuntimeVersion(FixedProvider())() == RuntimeVersion("synthetic-package", "2.0.0", "3.13.5")


def test_query_does_not_fabricate_version_on_failure() -> None:
    with pytest.raises(LookupError, match="synthetic missing package"):
        ReadRuntimeVersion(FailingProvider())()
