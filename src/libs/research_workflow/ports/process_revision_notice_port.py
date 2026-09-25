from typing import Protocol

from libs.research_workflow.dtos.revision_notice import RevisionNoticeOutcome


class ProcessRevisionNoticePort(Protocol):
    def __call__(self, outbox_id: str) -> RevisionNoticeOutcome: ...
