import hashlib
import json
from datetime import date, datetime, timezone

from libs.discovery.dtos.crossref_harvest import CrossrefPendingItem
from libs.scholarly_catalog.dtos.crossref_provider_revision import (
    CrossrefProviderRevisionDraft,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


class CrossrefProviderRevisionRules:
    SEMANTIC_VERSION = "crossref-bibliographic-semantic-v1"
    SEMANTIC_KEYS = frozenset(
        {
            "DOI",
            "abstract",
            "author",
            "container-title",
            "editor",
            "ISBN",
            "ISSN",
            "language",
            "license",
            "link",
            "published",
            "published-online",
            "published-print",
            "publisher",
            "relation",
            "resource",
            "short-container-title",
            "short-title",
            "subject",
            "subtitle",
            "title",
            "translator",
            "type",
            "update-to",
        }
    )

    @staticmethod
    def _canonical(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise CrossrefProviderProjectionError(
                "crossref_provider_item_mismatch"
            ) from exc

    @staticmethod
    def _sha(value: str) -> str:
        return hashlib.sha256(value.encode("ascii")).hexdigest()

    @classmethod
    def item(cls, pending: CrossrefPendingItem) -> dict[str, object]:
        if (
            not isinstance(pending, CrossrefPendingItem)
            or not isinstance(pending.canonical_json, str)
            or not isinstance(pending.canonical_sha256, str)
        ):
            raise CrossrefProviderProjectionError("crossref_provider_item_mismatch")
        try:
            value = json.loads(pending.canonical_json)
        except (json.JSONDecodeError, TypeError, RecursionError):
            raise CrossrefProviderProjectionError(
                "crossref_provider_item_mismatch"
            ) from None
        if (
            not isinstance(value, dict)
            or cls._canonical(value) != pending.canonical_json
            or cls._sha(pending.canonical_json) != pending.canonical_sha256
            or value.get("DOI") != pending.raw_doi
        ):
            raise CrossrefProviderProjectionError("crossref_provider_item_mismatch")
        return value

    @staticmethod
    def title(value: dict[str, object]) -> tuple[str | None, tuple[str, ...]]:
        raw = value.get("title")
        if raw is None:
            return None, ()
        if not isinstance(raw, list):
            return None, ("title_shape_invalid",)
        for item in raw:
            if isinstance(item, str) and item.strip():
                return item.strip(), ()
        return None, ("title_missing_text",)

    @staticmethod
    def provider_datetime(
        value: dict[str, object],
        key: str,
    ) -> tuple[str | None, tuple[str, ...]]:
        raw = value.get(key)
        if raw is None:
            return None, ()
        if not isinstance(raw, dict) or not isinstance(raw.get("date-time"), str):
            return None, (key + "_shape_invalid",)
        try:
            parsed = datetime.fromisoformat(raw["date-time"].replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
            return parsed.astimezone(timezone.utc).isoformat(), ()
        except (ValueError, OverflowError):
            return None, (key + "_time_invalid",)

    @staticmethod
    def published(
        value: dict[str, object],
    ) -> tuple[str | None, str | None, tuple[str, ...]]:
        raw = value.get("published")
        if raw is None:
            return None, None, ()
        if not isinstance(raw, dict):
            return None, None, ("published_shape_invalid",)
        parts = raw.get("date-parts")
        if (
            not isinstance(parts, list)
            or len(parts) != 1
            or not isinstance(parts[0], list)
            or not 1 <= len(parts[0]) <= 3
            or any(type(item) is not int for item in parts[0])
        ):
            return None, None, ("published_shape_invalid",)
        values = parts[0]
        year = values[0]
        if not 1 <= year <= 9999:
            return None, None, ("published_date_invalid",)
        if len(values) == 1:
            return f"{year:04d}", "year", ()
        month = values[1]
        if not 1 <= month <= 12:
            return None, None, ("published_date_invalid",)
        if len(values) == 2:
            return f"{year:04d}-{month:02d}", "month", ()
        day = values[2]
        try:
            date(year, month, day)
        except ValueError:
            return None, None, ("published_date_invalid",)
        return f"{year:04d}-{month:02d}-{day:02d}", "day", ()

    @classmethod
    def draft(
        cls,
        pending: CrossrefPendingItem,
        *,
        canonical_doi: str,
        observed_at: datetime,
    ) -> CrossrefProviderRevisionDraft:
        value = cls.item(pending)
        title, title_warnings = cls.title(value)
        indexed_at, indexed_warnings = cls.provider_datetime(value, "indexed")
        created_at, created_warnings = cls.provider_datetime(value, "created")
        deposited_at, deposited_warnings = cls.provider_datetime(value, "deposited")
        published_date, published_precision, published_warnings = cls.published(value)
        semantic = {
            key: value[key]
            for key in sorted(cls.SEMANTIC_KEYS)
            if key in value
        }
        semantic["DOI"] = canonical_doi
        warnings = tuple(
            sorted(
                {
                    *title_warnings,
                    *indexed_warnings,
                    *created_warnings,
                    *deposited_warnings,
                    *published_warnings,
                }
            )
        )
        return CrossrefProviderRevisionDraft(
            canonical_doi,
            pending.raw_doi or "",
            pending.canonical_sha256,
            cls._sha(cls._canonical(semantic)),
            cls.SEMANTIC_VERSION,
            pending.canonical_json,
            title,
            indexed_at,
            created_at,
            deposited_at,
            published_date,
            published_precision,
            warnings,
            pending.page_id,
            pending.ordinal,
            observed_at,
        )
