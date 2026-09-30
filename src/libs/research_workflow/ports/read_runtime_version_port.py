from abc import ABC, abstractmethod

from libs.research_workflow.dtos.runtime_version import RuntimeVersion


class ReadRuntimeVersionPort(ABC):
    @abstractmethod
    def __call__(self) -> RuntimeVersion:
        raise NotImplementedError
