import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from libs.research_workflow.dtos.scheduler import (
    CoverageGap,
    DeliverySchedule,
    HarvestBindingSchedule,
    KnownWorkflowJob,
    PendingDeliveryDispatch,
    SchedulerSnapshot,
)
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError


class SqliteSchedulerInputAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def gap(kind: str, identity: str, reason: str) -> CoverageGap:
        return CoverageGap(kind, identity, reason)

    @staticmethod
    def _instant(value: object, code: str) -> datetime:
        if not isinstance(value, str):
            raise WorkflowJobError(code)
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise WorkflowJobError(code) from None
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise WorkflowJobError(code)
        return moment.astimezone(timezone.utc)

    @staticmethod
    def _strict_json(value: object, code: str) -> object:
        if not isinstance(value, str) or not value or len(value) > 1024 * 1024:
            raise WorkflowJobError(code)

        def pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in rows:
                if key in result:
                    raise ValueError("duplicate json key")
                result[key] = item
            return result

        try:
            return json.loads(
                value,
                object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            raise WorkflowJobError(code) from None

    @classmethod
    def _sources(cls, value: object, code: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not value or len(value) > 64:
            raise WorkflowJobError(code)
        result = []
        seen = set()
        for item in value:
            if (
                not isinstance(item, str)
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item) is None
                or item in seen
            ):
                raise WorkflowJobError(code)
            seen.add(item)
            result.append(item)
        return tuple(result)

    @classmethod
    def _active_bindings(
        cls,
        connection: sqlite3.Connection,
        gaps: list[CoverageGap],
    ) -> list[HarvestBindingSchedule]:
        rows = connection.execute(
            "SELECT p.id AS profile_id,p.published_revision,r.filters_json,"
            "d.domain_id,d.domain_revision,dd.definition_json "
            "FROM watch_profiles p "
            "JOIN watch_profile_revisions r "
            "ON r.profile_id=p.id AND r.revision=p.published_revision "
            "JOIN watch_profile_domains d "
            "ON d.profile_id=p.id AND d.revision=p.published_revision "
            "JOIN domain_definitions dd "
            "ON dd.id=d.domain_id AND dd.revision=d.domain_revision "
            "WHERE p.lifecycle='active' AND p.published_revision IS NOT NULL "
            "ORDER BY p.id,d.domain_id"
        ).fetchall()
        bindings: list[HarvestBindingSchedule] = []
        for row in rows:
            identity = (
                f"{row['profile_id']}:{row['published_revision']}:"
                f"{row['domain_id']}:{row['domain_revision']}"
            )
            try:
                filters = cls._strict_json(row["filters_json"], "invalid_profile_scheduler_input")
                definition = cls._strict_json(
                    row["definition_json"],
                    "invalid_domain_scheduler_input",
                )
                if not isinstance(filters, dict) or not isinstance(definition, dict):
                    raise WorkflowJobError("invalid_scheduler_configuration")
                profile_sources = cls._sources(
                    filters.get("sources"),
                    "invalid_profile_scheduler_input",
                )
                domain_sources = cls._sources(
                    definition.get("sources"),
                    "invalid_domain_scheduler_input",
                )
            except WorkflowJobError as exc:
                gaps.append(cls.gap("configuration", identity, str(exc)))
                continue

            allowed = sorted(set(profile_sources) & set(domain_sources))
            if not allowed:
                gaps.append(cls.gap("harvest", identity, "no_selected_source"))
                continue
            for source in allowed:
                binding_key = identity + ":" + source
                bindings.append(
                    HarvestBindingSchedule(
                        binding_key=binding_key,
                        profile_id=row["profile_id"],
                        profile_revision=row["published_revision"],
                        domain_id=row["domain_id"],
                        domain_revision=row["domain_revision"],
                        source_id=source,
                        last_succeeded_window_end=None,
                    )
                )
        return bindings

    @classmethod
    def _workflow_history(
        cls,
        connection: sqlite3.Connection,
    ) -> tuple[dict[str, datetime], tuple[KnownWorkflowJob, ...], dict[str, datetime]]:
        last_harvest: dict[str, datetime] = {}
        known: list[KnownWorkflowJob] = []
        last_digest: dict[str, datetime] = {}
        rows = connection.execute(
            "SELECT job_kind,business_key,input_json,state FROM workflow_jobs "
            "WHERE job_kind IN ('harvest_window','prepare_digest') "
            "ORDER BY business_key"
        ).fetchall()
        for row in rows:
            try:
                data = cls._strict_json(row["input_json"], "corrupt_scheduler_job")
            except WorkflowJobError:
                raise WorkflowJobError("corrupt_scheduler_job") from None
            if not isinstance(data, dict):
                raise WorkflowJobError("corrupt_scheduler_job")
            if row["job_kind"] == "harvest_window":
                binding_key = data.get("binding_key")
                window_end = data.get("window_end")
                if not isinstance(binding_key, str):
                    raise WorkflowJobError("corrupt_scheduler_job")
                if row["state"] == "succeeded":
                    end = cls._instant(window_end, "corrupt_scheduler_job")
                    prior = last_harvest.get(binding_key)
                    if prior is None or end > prior:
                        last_harvest[binding_key] = end
                elif row["state"] in {"pending", "running", "failed", "awaiting_external"}:
                    known.append(
                        KnownWorkflowJob(
                            row["business_key"],
                            row["state"],
                            binding_key,
                        )
                    )
            else:
                subscription_id = data.get("subscription_id")
                cutoff_at = data.get("cutoff_at")
                if not isinstance(subscription_id, str):
                    raise WorkflowJobError("corrupt_scheduler_job")
                cutoff = cls._instant(cutoff_at, "corrupt_scheduler_job")
                prior = last_digest.get(subscription_id)
                if prior is None or cutoff > prior:
                    last_digest[subscription_id] = cutoff
        return last_harvest, tuple(known), last_digest

    @classmethod
    def _delivery_schedules(
        cls,
        connection: sqlite3.Connection,
        last_digest: dict[str, datetime],
        gaps: list[CoverageGap],
    ) -> list[DeliverySchedule]:
        rows = connection.execute(
            "SELECT id,channel,timezone,schedule_json FROM delivery_subscriptions "
            "WHERE enabled=1 ORDER BY id"
        ).fetchall()
        schedules = []
        for row in rows:
            identity = row["id"]
            if row["channel"] != "email":
                gaps.append(cls.gap("delivery", identity, "delivery_channel_not_supported"))
                continue
            try:
                data = cls._strict_json(row["schedule_json"], "invalid_delivery_schedule")
                if (
                    not isinstance(data, dict)
                    or set(data) != {"kind", "local_time"}
                    or data["kind"] != "daily"
                    or not isinstance(data["local_time"], str)
                    or re.fullmatch(
                        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]",
                        data["local_time"],
                    )
                    is None
                ):
                    raise WorkflowJobError("invalid_delivery_schedule")
                if not isinstance(row["timezone"], str) or not row["timezone"]:
                    raise WorkflowJobError("invalid_delivery_timezone")
                ZoneInfo(row["timezone"])
            except (
                WorkflowJobError,
                ZoneInfoNotFoundError,
                ValueError,
                TypeError,
            ):
                gaps.append(cls.gap("delivery", identity, "invalid_delivery_schedule"))
                continue
            schedules.append(
                DeliverySchedule(
                    subscription_id=identity,
                    timezone=row["timezone"],
                    local_time=data["local_time"],
                    last_scheduled_cutoff=last_digest.get(identity),
                )
            )
        return schedules

    @classmethod
    def _pending_delivery_outboxes(
        cls,
        connection: sqlite3.Connection,
    ) -> tuple[PendingDeliveryDispatch, ...]:
        rows = connection.execute(
            "SELECT o.id AS outbox_id,d.state AS digest_state,"
            "d.subscription_id,d.period_key,"
            "(SELECT w.input_json FROM workflow_jobs w "
            "WHERE w.job_kind='prepare_digest' "
            "AND w.business_key=('digest:'||d.subscription_id||':'||d.period_key) "
            "LIMIT 1) AS prepare_input_json,"
            "(SELECT w.state FROM workflow_jobs w "
            "WHERE w.job_kind='dispatch_digest' "
            "AND w.business_key=('dispatch:'||o.id) LIMIT 1) AS dispatch_job_state "
            "FROM delivery_outbox o "
            "LEFT JOIN digests d ON d.id=o.digest_id "
            "WHERE o.state='pending' ORDER BY o.id"
        ).fetchall()
        result = []
        for row in rows:
            outbox_id = row["outbox_id"]
            if (
                not isinstance(outbox_id, str)
                or not outbox_id
                or row["digest_state"] != "queued"
            ):
                raise WorkflowJobError("delivery_outbox_state_corrupt")
            dispatch_state = row["dispatch_job_state"]
            if dispatch_state is not None:
                if dispatch_state in {
                    "pending",
                    "running",
                    "failed",
                    "awaiting_external",
                }:
                    continue
                raise WorkflowJobError("delivery_dispatch_job_state_corrupt")
            prepare_input = row["prepare_input_json"]
            canonical = None
            if prepare_input is not None:
                data = cls._strict_json(
                    prepare_input,
                    "corrupt_scheduler_job",
                )
                required = {
                    "subscription_id",
                    "period_key",
                    "period_start",
                    "cutoff_at",
                    "coverage_gaps",
                }
                if (
                    not isinstance(data, dict)
                    or set(data) != required
                    or data["subscription_id"] != row["subscription_id"]
                    or data["period_key"] != row["period_key"]
                ):
                    raise WorkflowJobError("corrupt_scheduler_job")
                canonical = json.dumps(
                    data,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            result.append(PendingDeliveryDispatch(outbox_id, canonical))
        return tuple(result)

    def read(self, now: datetime) -> SchedulerSnapshot:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise WorkflowJobError("invalid_scheduler_time")
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise WorkflowJobError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            gaps: list[CoverageGap] = []
            bindings = self._active_bindings(connection, gaps)
            last_harvest, known, last_digest = self._workflow_history(connection)
            schedules = self._delivery_schedules(connection, last_digest, gaps)
            pending_outboxes = self._pending_delivery_outboxes(connection)
            bindings = [
                HarvestBindingSchedule(
                    item.binding_key,
                    item.profile_id,
                    item.profile_revision,
                    item.domain_id,
                    item.domain_revision,
                    item.source_id,
                    last_harvest.get(item.binding_key),
                )
                for item in bindings
            ]
            connection.commit()
            return SchedulerSnapshot(
                harvest_bindings=tuple(bindings),
                delivery_schedules=tuple(schedules),
                known_jobs=known,
                input_gaps=tuple(
                    sorted(gaps, key=lambda gap: (gap.kind, gap.identity, gap.reason))
                ),
                pending_delivery_outboxes=pending_outboxes,
            )
        except WorkflowJobError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise WorkflowJobError("scheduler_database_error") from exc
        finally:
            if connection is not None:
                connection.close()
