import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timezone

from libs.research_workflow.dtos.workflow_job import (
    CompleteWorkflowJob,
    EnqueueWorkflowJob,
)
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError


class WorkflowJobRules:
    RETRYABLE_STATES = frozenset({"failed", "awaiting_external"})
    COMPLETION_STATES = frozenset(
        {"succeeded", "failed", "cancelled", "budget_blocked", "awaiting_external"}
    )

    @staticmethod
    def text(value: object, code: str, *, maximum: int = 1024) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
            raise WorkflowJobError(code)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise WorkflowJobError(code) from None
        return value

    @staticmethod
    def instant(value: object, code: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise WorkflowJobError(code)
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise WorkflowJobError(code) from None

    @staticmethod
    def instant_text(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def fingerprint(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise WorkflowJobError("invalid_job_input_fingerprint")
        return value

    @classmethod
    def canonical_input(cls, value: object) -> str:
        if not isinstance(value, str) or not value or len(value) > 1024 * 1024:
            raise WorkflowJobError("invalid_job_input")
        try:
            parsed = json.loads(
                value,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
            canonical = json.dumps(
                parsed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            canonical.encode("utf-8")
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise WorkflowJobError("invalid_job_input") from exc
        return canonical

    @classmethod
    def enqueue(cls, request: EnqueueWorkflowJob) -> EnqueueWorkflowJob:
        if not isinstance(request, EnqueueWorkflowJob):
            raise WorkflowJobError("invalid_job_request")
        job_kind = cls.text(request.job_kind, "invalid_job_kind", maximum=128)
        business_key = cls.text(request.business_key, "invalid_business_key", maximum=512)
        input_json = cls.canonical_input(request.input_json)
        fingerprint = cls.fingerprint(request.input_fingerprint)
        due_at = cls.instant(request.due_at, "invalid_job_due_at")
        created_at = cls.instant(request.created_at, "invalid_job_created_at")
        return replace(
            request,
            job_kind=job_kind,
            business_key=business_key,
            input_json=input_json,
            input_fingerprint=fingerprint,
            due_at=due_at,
            created_at=created_at,
        )

    @classmethod
    def owner(cls, owner_id: object) -> str:
        return cls.text(owner_id, "invalid_lease_owner", maximum=256)

    @staticmethod
    def lease_seconds(value: object) -> int:
        if type(value) is not int or not 1 <= value <= 86_400:
            raise WorkflowJobError("invalid_lease_duration")
        return value

    @classmethod
    def completion(cls, request: CompleteWorkflowJob) -> CompleteWorkflowJob:
        if not isinstance(request, CompleteWorkflowJob):
            raise WorkflowJobError("invalid_job_completion")
        job_id = cls.text(request.job_id, "invalid_job_id", maximum=128)
        owner_id = cls.owner(request.owner_id)
        if type(request.fencing_token) is not int or request.fencing_token < 1:
            raise WorkflowJobError("invalid_fencing_token")
        if request.state not in cls.COMPLETION_STATES:
            raise WorkflowJobError("invalid_job_completion_state")
        if request.error_code is not None:
            cls.text(request.error_code, "invalid_job_error_code", maximum=256)
        finished_at = cls.instant(request.finished_at, "invalid_job_finished_at")
        next_due_at = request.next_due_at
        if request.state in cls.RETRYABLE_STATES:
            if next_due_at is None:
                raise WorkflowJobError("retry_due_at_required")
            next_due_at = cls.instant(next_due_at, "invalid_job_due_at")
            if next_due_at <= finished_at:
                raise WorkflowJobError("invalid_job_due_at")
        elif next_due_at is not None:
            raise WorkflowJobError("unexpected_next_due_at")
        return replace(
            request,
            job_id=job_id,
            owner_id=owner_id,
            finished_at=finished_at,
            next_due_at=next_due_at,
        )

    @staticmethod
    def job_id(business_key: str) -> str:
        return "job:" + hashlib.sha256(("workflow-job\0" + business_key).encode("utf-8")).hexdigest()

    @staticmethod
    def attempt_id(job_id: str, attempt_no: int, fencing_token: int) -> str:
        raw = f"workflow-attempt\0{job_id}\0{attempt_no}\0{fencing_token}"
        return "job-attempt:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
