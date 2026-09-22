import json
import math
from datetime import datetime, timedelta

from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.watch_profiles.dtos.profile_revision import ProfileRevision


class HarvestRunRules:
    @staticmethod
    def limits(max_pages: int, retry_failed: bool) -> None:
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise HarvestWorkflowError("invalid_run_limit")
        if type(retry_failed) is not bool:
            raise HarvestWorkflowError("invalid_retry_policy")

    @staticmethod
    def profile_matches(query: SourceQueryInput, profile: ProfileRevision) -> bool:
        if not isinstance(profile, ProfileRevision):
            raise HarvestWorkflowError("invalid_profile_snapshot")
        if (
            profile.profile_id != query.profile_id
            or type(profile.revision) is not int or type(profile.current_revision) is not int
            or profile.revision != query.profile_revision or profile.current_revision != query.profile_revision
            or profile.fingerprint != query.profile_fingerprint or profile.lifecycle != "active"
            or profile.scope_text != query.scope_text
            or (query.domain.domain_id, query.domain.revision) not in profile.domains
        ):
            return False
        try:
            data = json.loads(profile.filters_json)
            if not isinstance(data, dict) or set(data) != {
                "sources", "include", "exclude", "languages", "free_only", "allow_preprints"
            }:
                return False
            for name, expected in (
                ("sources", query.profile_sources), ("include", query.profile_include),
                ("exclude", query.profile_exclude), ("languages", query.languages),
            ):
                values = data[name]
                if (not isinstance(values, list) or any(not isinstance(v, str) for v in values)
                        or sorted(values) != sorted(expected)):
                    return False
            return (type(data["free_only"]) is bool and type(data["allow_preprints"]) is bool
                    and data["free_only"] == query.free_only and data["allow_preprints"] == query.allow_preprints)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise HarvestWorkflowError("invalid_profile_snapshot") from None

    @staticmethod
    def retry_stop(attempt: HarvestAttempt, retry_failed: bool, now: datetime) -> str | None:
        try:
            if not isinstance(attempt.capture_json, str) or len(attempt.capture_json) > 65536:
                raise HarvestWorkflowError("invalid_retry_metadata")
            capture = json.loads(attempt.capture_json)
            if not isinstance(capture, dict) or type(capture.get("retryable")) is not bool:
                raise HarvestWorkflowError("invalid_retry_metadata")
            if capture["failure_code"] is None:
                return "processing_failed"
            if not capture["retryable"]:
                return "source_failed"
            if not retry_failed:
                return "retry_required"
            delay = capture["retry_after_seconds"]
            if delay is None:
                delay = 0.0
            if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
                raise HarvestWorkflowError("invalid_retry_metadata")
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                raise HarvestWorkflowError("invalid_retry_time")
            finished = datetime.fromisoformat(attempt.finished_at or "")
            if finished.tzinfo is None or finished.utcoffset() is None:
                raise HarvestWorkflowError("invalid_retry_time")
            return "cooldown" if now < finished + timedelta(seconds=delay) else None
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise HarvestWorkflowError("invalid_retry_metadata") from None
