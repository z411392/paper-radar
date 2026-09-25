from typing import Protocol

from libs.research_workflow.dtos.revision_notice import (
    RevisionNoticeOutcome,
    RevisionNoticeRequest,
)


class ProcessRevisionNoticePort(Protocol):
    def __call__(self, request: RevisionNoticeRequest) -> RevisionNoticeOutcome: ...
