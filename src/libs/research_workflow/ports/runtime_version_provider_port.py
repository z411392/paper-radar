from abc import ABC, abstractmethod

from libs.research_workflow.dtos.runtime_version import RuntimeVersion


class RuntimeVersionProviderPort(ABC):
    @abstractmethod
    def read(self) -> RuntimeVersion:
        raise NotImplementedError
