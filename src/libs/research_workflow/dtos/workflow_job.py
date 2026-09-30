from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EnqueueWorkflowJob:
    job_kind: str
    business_key: str
    input_json: str
    input_fingerprint: str
    due_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class EnqueuedWorkflowJob:
    job_id: str
    replayed: bool


@dataclass(frozen=True)
class WorkflowJobLease:
    job_id: str
    job_kind: str
    business_key: str
    input_json: str
    input_fingerprint: str
    owner_id: str
    fencing_token: int
    attempt_id: str
    attempt_no: int
    lease_until: datetime


@dataclass(frozen=True)
class CompleteWorkflowJob:
    job_id: str
    owner_id: str
    fencing_token: int
    state: str
    error_code: str | None
    finished_at: datetime
    next_due_at: datetime | None


@dataclass(frozen=True)
class WorkflowJobCompletion:
    job_id: str
    state: str
    fencing_token: int
    replayed: bool
