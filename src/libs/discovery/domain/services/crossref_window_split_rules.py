import hashlib
import json
from datetime import datetime, timezone

from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.exceptions.crossref_window_split_error import CrossrefWindowSplitError


class CrossrefWindowSplitRules:
    REASONS = frozenset({"volume_budget", "page_budget", "time_budget", "operator"})
    MAX_DEPTH = 32  # Local topology resource bound, not a provider limit.

    @staticmethod
    def instant(value: object) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise CrossrefWindowSplitError("invalid_crossref_split_time")
        try:
            if value.utcoffset() is None:
                raise ValueError
            result = value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise CrossrefWindowSplitError("invalid_crossref_split_time") from None
        return result

    @classmethod
    def boundary(cls, plan: CrossrefWindowPlan, value: object) -> datetime:
        result = cls.instant(value)
        if result.microsecond or not plan.definition.from_index < result < plan.definition.until_index:
            raise CrossrefWindowSplitError("invalid_crossref_split_boundary")
        return result

    @classmethod
    def reason(cls, value: object) -> str:
        if not isinstance(value, str) or value not in cls.REASONS:
            raise CrossrefWindowSplitError("invalid_crossref_split_reason")
        return value

    @staticmethod
    def window_id(plan: CrossrefWindowPlan) -> str:
        content = json.dumps((plan.definition.binding_key, plan.query_fingerprint),
                             ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        return "crossref-window:" + hashlib.sha256(content).hexdigest()

    @staticmethod
    def values(plan: CrossrefWindowPlan) -> tuple[str, str, str, str, str, int]:
        definition = plan.definition
        return (definition.binding_key, plan.query_fingerprint, definition.config_version,
                definition.from_index.isoformat(), definition.until_index.isoformat(), definition.rows)
