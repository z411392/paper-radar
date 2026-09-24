import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from libs.research_workflow.dtos.scheduler import (
    CoverageGap,
    DeliverySchedule,
    HarvestBindingSchedule,
    SchedulerPlan,
    SchedulerSnapshot,
)
from libs.research_workflow.dtos.workflow_job import EnqueueWorkflowJob
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError


class PlanCatchupJobs:
    HARVEST_INTERVAL = timedelta(hours=24)
    SUPPORTED_SOURCES = frozenset({"arxiv", "pubmed", "crossref"})

    @staticmethod
    def _instant(value: object, code: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise WorkflowJobError(code)
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise WorkflowJobError(code) from None

    @staticmethod
    def _canonical(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise WorkflowJobError("invalid_scheduler_input") from exc

    @staticmethod
    def decode(value: str) -> dict:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise WorkflowJobError("invalid_scheduler_input")
        return data

    @classmethod
    def _job(
        cls,
        *,
        job_kind: str,
        business_key: str,
        payload: dict,
        due_at: datetime,
        created_at: datetime,
    ) -> EnqueueWorkflowJob:
        encoded = cls._canonical(payload)
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return EnqueueWorkflowJob(
            job_kind=job_kind,
            business_key=business_key,
            input_json=encoded,
            input_fingerprint=fingerprint,
            due_at=due_at,
            created_at=created_at,
        )

    @classmethod
    def harvest_job(
        cls,
        schedule: HarvestBindingSchedule,
        window_end: datetime,
    ) -> EnqueueWorkflowJob:
        end = cls._instant(window_end, "invalid_harvest_window")
        if schedule.last_succeeded_window_end is None:
            start = end - cls.HARVEST_INTERVAL
        else:
            start = cls._instant(
                schedule.last_succeeded_window_end,
                "invalid_harvest_window",
            )
        payload = {
            "binding_key": schedule.binding_key,
            "profile_id": schedule.profile_id,
            "profile_revision": schedule.profile_revision,
            "domain_id": schedule.domain_id,
            "domain_revision": schedule.domain_revision,
            "source_id": schedule.source_id,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }
        identity = hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()
        return cls._job(
            job_kind="harvest_window",
            business_key="harvest:" + identity,
            payload=payload,
            due_at=end,
            created_at=end,
        )

    @staticmethod
    def _local_time(value: str) -> time:
        if not isinstance(value, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
            raise WorkflowJobError("invalid_delivery_schedule")
        hour, minute = (int(part) for part in value.split(":"))
        return time(hour=hour, minute=minute)

    @classmethod
    def _cutoff(
        cls,
        schedule: DeliverySchedule,
        now: datetime,
    ) -> tuple[str, datetime]:
        try:
            zone = ZoneInfo(schedule.timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise WorkflowJobError("invalid_delivery_timezone") from None
        local_now = now.astimezone(zone)
        cutoff_time = cls._local_time(schedule.local_time)
        day: date = local_now.date()
        candidate = datetime.combine(day, cutoff_time, tzinfo=zone)
        if local_now < candidate:
            candidate -= timedelta(days=1)
        return candidate.date().isoformat(), candidate.astimezone(timezone.utc)

    @classmethod
    def _digest_job(
        cls,
        schedule: DeliverySchedule,
        *,
        now: datetime,
        gaps: tuple[CoverageGap, ...],
    ) -> EnqueueWorkflowJob | None:
        period_key, cutoff = cls._cutoff(schedule, now)
        last = (
            None
            if schedule.last_scheduled_cutoff is None
            else cls._instant(schedule.last_scheduled_cutoff, "invalid_delivery_schedule")
        )
        if last is not None and last >= cutoff:
            return None
        start = last if last is not None else cutoff - timedelta(hours=24)
        payload = {
            "subscription_id": schedule.subscription_id,
            "period_key": period_key,
            "period_start": start.isoformat(),
            "cutoff_at": cutoff.isoformat(),
            "coverage_gaps": [
                {
                    "identity": gap.identity,
                    "kind": gap.kind,
                    "reason": gap.reason,
                }
                for gap in gaps
            ],
        }
        return cls._job(
            job_kind="prepare_digest",
            business_key=f"digest:{schedule.subscription_id}:{period_key}",
            payload=payload,
            due_at=cutoff,
            created_at=now,
        )

    @classmethod
    def _harvest_window_end(
        cls,
        schedule: HarvestBindingSchedule,
        current: datetime,
    ) -> datetime | None:
        last = (
            None
            if schedule.last_succeeded_window_end is None
            else cls._instant(schedule.last_succeeded_window_end, "invalid_harvest_window")
        )
        if schedule.source_id == "pubmed":
            boundary = current.replace(hour=0, minute=0, second=0, microsecond=0)
            if last is None:
                return boundary
            if last != last.replace(hour=0, minute=0, second=0, microsecond=0):
                raise WorkflowJobError("invalid_pubmed_harvest_window")
            return last + cls.HARVEST_INTERVAL if last + cls.HARVEST_INTERVAL <= boundary else None

        if last is None:
            return current
        return last + cls.HARVEST_INTERVAL if last + cls.HARVEST_INTERVAL <= current else None

    @staticmethod
    def _binding_order(schedule: HarvestBindingSchedule) -> tuple[float, str]:
        if schedule.last_succeeded_window_end is None:
            return (float("-inf"), schedule.binding_key)
        return (
            schedule.last_succeeded_window_end.timestamp(),
            schedule.binding_key,
        )

    def __call__(self, snapshot: SchedulerSnapshot, *, now: datetime) -> SchedulerPlan:
        if not isinstance(snapshot, SchedulerSnapshot):
            raise WorkflowJobError("invalid_scheduler_snapshot")
        current = self._instant(now, "invalid_scheduler_time")
        active_by_binding = {}
        for item in snapshot.known_jobs:
            if item.binding_key in active_by_binding:
                raise WorkflowJobError("duplicate_active_binding_job")
            if item.state not in {"pending", "running", "failed", "awaiting_external"}:
                raise WorkflowJobError("invalid_scheduler_job_state")
            active_by_binding[item.binding_key] = item
        gaps = list(snapshot.input_gaps)
        jobs: list[EnqueueWorkflowJob] = []
        blocks_digest = False

        for schedule in sorted(snapshot.harvest_bindings, key=self._binding_order):
            if schedule.source_id not in self.SUPPORTED_SOURCES:
                gaps.append(
                    CoverageGap(
                        "harvest",
                        schedule.binding_key,
                        "source_scheduler_not_supported",
                    )
                )
                continue

            active = active_by_binding.get(schedule.binding_key)
            if active is not None:
                if active.state == "failed":
                    gaps.append(
                        CoverageGap("harvest", schedule.binding_key, "harvest_window_failed")
                    )
                elif active.state == "awaiting_external":
                    gaps.append(
                        CoverageGap(
                            "harvest",
                            schedule.binding_key,
                            "harvest_window_awaiting_external",
                        )
                    )
                else:
                    blocks_digest = True
                continue

            window_end = self._harvest_window_end(schedule, current)
            if window_end is None:
                continue
            jobs.append(self.harvest_job(schedule, window_end))
            blocks_digest = True

        gap_tuple = tuple(sorted(gaps, key=lambda gap: (gap.kind, gap.identity, gap.reason)))
        if not blocks_digest:
            for schedule in sorted(
                snapshot.delivery_schedules,
                key=lambda item: item.subscription_id,
            ):
                job = self._digest_job(schedule, now=current, gaps=gap_tuple)
                if job is not None:
                    jobs.append(job)

        return SchedulerPlan(tuple(jobs), gap_tuple, blocks_digest)
