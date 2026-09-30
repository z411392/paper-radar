import hashlib
import json
import re
from datetime import datetime, timezone

from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.crossref_repair import CrossrefRepairPolicy
from libs.discovery.exceptions.crossref_repair_error import CrossrefRepairError


class CrossrefRepairRules:
    REASONS = frozenset({"repair_pending", "periodic_recent_window", "operator"})

    @staticmethod
    def text(value: object, code: str, maximum: int = 4096) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or "\0" in value
        ):
            raise CrossrefRepairError(code)
        try:
            if len(value.encode("utf-8")) > maximum:
                raise CrossrefRepairError(code)
        except UnicodeEncodeError:
            raise CrossrefRepairError(code) from None
        return value

    @staticmethod
    def fingerprint(value: object, code: str = "invalid_crossref_repair_plan") -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise CrossrefRepairError(code)
        return value

    @staticmethod
    def instant(value: object, code: str = "invalid_crossref_repair_time") -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise CrossrefRepairError(code)
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise CrossrefRepairError(code) from None

    @staticmethod
    def parse_instant(value: object) -> datetime:
        if not isinstance(value, str):
            raise CrossrefRepairError("crossref_repair_state_corrupt")
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise CrossrefRepairError("crossref_repair_state_corrupt") from None
        if result.tzinfo is None or result.utcoffset() is None:
            raise CrossrefRepairError("crossref_repair_state_corrupt")
        return result.astimezone(timezone.utc)

    @staticmethod
    def canonical(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise CrossrefRepairError("invalid_crossref_repair_plan") from exc

    @classmethod
    def hash(cls, *parts: object) -> str:
        return hashlib.sha256(cls.canonical(parts).encode("ascii")).hexdigest()

    @classmethod
    def plan(
        cls,
        plan: CrossrefWindowPlan,
    ) -> tuple[str, str, int, str, str, str, str]:
        if not isinstance(plan, CrossrefWindowPlan):
            raise CrossrefRepairError("invalid_crossref_repair_plan")
        definition = plan.definition
        binding = cls.text(
            getattr(definition, "binding_key", None),
            "invalid_crossref_repair_plan",
            512,
        )
        config = cls.text(
            getattr(definition, "config_version", None),
            "invalid_crossref_repair_plan",
            128,
        )
        scope = cls.text(
            getattr(definition, "scope_query", None),
            "invalid_crossref_repair_plan",
            4096,
        )
        rows = getattr(definition, "rows", None)
        if type(rows) is not int or not 1 <= rows <= 1000:
            raise CrossrefRepairError("invalid_crossref_repair_plan")
        query = cls.fingerprint(plan.query_fingerprint)
        parameters = cls.fingerprint(plan.parameters_fingerprint)
        start = cls.instant(
            getattr(definition, "from_index", None),
            "invalid_crossref_repair_plan",
        ).isoformat()
        end = cls.instant(
            getattr(definition, "until_index", None),
            "invalid_crossref_repair_plan",
        ).isoformat()
        if start >= end:
            raise CrossrefRepairError("invalid_crossref_repair_plan")
        scope_sha = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        return binding, config, rows, scope_sha, query, parameters, start + "\0" + end

    @classmethod
    def stream_id(cls, plan: CrossrefWindowPlan) -> str:
        binding, config, rows, scope_sha, _, _, _ = cls.plan(plan)
        return "crossref-stream:" + cls.hash(binding, config, rows, scope_sha)

    @staticmethod
    def policy(value: CrossrefRepairPolicy) -> CrossrefRepairPolicy:
        if not isinstance(value, CrossrefRepairPolicy):
            raise CrossrefRepairError("invalid_crossref_repair_policy")
        fields = (
            value.safety_lag_seconds,
            value.lookback_windows,
            value.periodic_repair_after_seconds,
            value.max_windows,
            value.repair_retry_after_seconds,
            value.max_consecutive_failures,
        )
        if any(type(item) is not int for item in fields):
            raise CrossrefRepairError("invalid_crossref_repair_policy")
        if (
            not 0 <= value.safety_lag_seconds <= 30 * 86400
            or not 1 <= value.lookback_windows <= 365
            or not 60 <= value.periodic_repair_after_seconds <= 365 * 86400
            or not 1 <= value.max_windows <= 100
            or not 60 <= value.repair_retry_after_seconds <= 30 * 86400
            or not 1 <= value.max_consecutive_failures <= 100
        ):
            raise CrossrefRepairError("invalid_crossref_repair_policy")
        return value

    @classmethod
    def reason(cls, value: object) -> str:
        if value not in cls.REASONS:
            raise CrossrefRepairError("invalid_crossref_repair_reason")
        assert isinstance(value, str)
        return value
