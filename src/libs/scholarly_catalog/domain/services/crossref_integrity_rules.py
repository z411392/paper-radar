import json
from datetime import date, datetime, timezone

from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityEntry,
    CrossrefIntegrityGap,
)


class CrossrefIntegrityRules:
    CORRECTIONS = frozenset({"correction", "corrigendum", "erratum"})
    RETRACTIONS = frozenset({"retraction", "partial retraction"})
    EXPRESSIONS = frozenset({"expression of concern"})
    REINSTATEMENTS = frozenset({"reinstatement"})

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _text(value: object, maximum: int) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            return None
        try:
            if len(value.encode("utf-8")) > maximum:
                return None
        except UnicodeEncodeError:
            return None
        return value

    @classmethod
    def _classify(cls, value: str | None) -> str:
        if value is None:
            return "other_update"
        normalized = value.lower().replace("-", " ").replace("_", " ")
        normalized = " ".join(normalized.split())
        if normalized in cls.CORRECTIONS:
            return "correction"
        if normalized in cls.RETRACTIONS:
            return "retraction"
        if normalized in cls.EXPRESSIONS:
            return "expression_of_concern"
        if normalized in cls.REINSTATEMENTS:
            return "reinstatement"
        return "other_update"

    @classmethod
    def _updated(
        cls,
        value: object,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        if value is None:
            return None, None, None, None
        raw = cls._canonical(value)
        if not isinstance(value, dict):
            return None, None, raw, "integrity_updated_invalid"
        date_time = value.get("date-time")
        if isinstance(date_time, str):
            try:
                parsed = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError
                return (
                    parsed.astimezone(timezone.utc).isoformat(),
                    "second",
                    raw,
                    None,
                )
            except (ValueError, OverflowError):
                pass
        parts = value.get("date-parts")
        if (
            isinstance(parts, list)
            and len(parts) == 1
            and isinstance(parts[0], list)
            and 1 <= len(parts[0]) <= 3
            and all(type(item) is int for item in parts[0])
        ):
            values = parts[0]
            year = values[0]
            if 1 <= year <= 9999:
                if len(values) == 1:
                    return f"{year:04d}", "year", raw, None
                month = values[1]
                if 1 <= month <= 12:
                    if len(values) == 2:
                        return f"{year:04d}-{month:02d}", "month", raw, None
                    day = values[2]
                    try:
                        date(year, month, day)
                    except ValueError:
                        pass
                    else:
                        return (
                            f"{year:04d}-{month:02d}-{day:02d}",
                            "day",
                            raw,
                            None,
                        )
        return None, None, raw, "integrity_updated_invalid"

    @classmethod
    def _direction(
        cls,
        item: dict[str, object],
        key: str,
        wire_direction: str,
    ) -> tuple[list[CrossrefIntegrityEntry], list[CrossrefIntegrityGap]]:
        raw_updates = item.get(key)
        if raw_updates is None:
            return [], []
        if not isinstance(raw_updates, list):
            return [], [
                CrossrefIntegrityGap(
                    key,
                    "integrity_updates_not_list",
                    cls._canonical(raw_updates),
                )
            ]
        entries: list[CrossrefIntegrityEntry] = []
        gaps: list[CrossrefIntegrityGap] = []
        for ordinal, raw in enumerate(raw_updates):
            path = f"{key}[{ordinal}]"
            if not isinstance(raw, dict):
                gaps.append(
                    CrossrefIntegrityGap(
                        path,
                        "integrity_update_not_object",
                        cls._canonical(raw),
                    )
                )
                continue
            raw_json = cls._canonical(raw)
            counterparty = cls._text(raw.get("DOI"), 2048)
            type_raw = cls._text(raw.get("type"), 256)
            source_raw = cls._text(raw.get("source"), 256)
            label_raw = cls._text(raw.get("label"), 1024)
            record_id_raw_json = (
                cls._canonical(raw["record-id"]) if "record-id" in raw else None
            )
            if raw.get("DOI") is not None and counterparty is None:
                gaps.append(
                    CrossrefIntegrityGap(
                        path + ".DOI",
                        "integrity_counterparty_doi_invalid_text",
                        cls._canonical(raw.get("DOI")),
                    )
                )
            for field, value, parsed in (
                ("type", raw.get("type"), type_raw),
                ("source", raw.get("source"), source_raw),
                ("label", raw.get("label"), label_raw),
            ):
                if value is not None and parsed is None:
                    gaps.append(
                        CrossrefIntegrityGap(
                            path + "." + field,
                            "integrity_field_invalid",
                            cls._canonical(value),
                        )
                    )
            updated, precision, updated_raw, updated_error = cls._updated(
                raw.get("updated")
            )
            if updated_error is not None:
                gaps.append(
                    CrossrefIntegrityGap(
                        path + ".updated",
                        updated_error,
                        updated_raw or "null",
                    )
                )
            entries.append(
                CrossrefIntegrityEntry(
                    wire_direction,
                    ordinal,
                    counterparty,
                    type_raw,
                    source_raw,
                    label_raw,
                    record_id_raw_json,
                    cls._classify(type_raw),
                    updated,
                    precision,
                    updated_raw,
                    raw_json,
                )
            )
        return entries, gaps

    @classmethod
    def extract(
        cls,
        item: dict[str, object],
    ) -> tuple[tuple[CrossrefIntegrityEntry, ...], tuple[CrossrefIntegrityGap, ...]]:
        entries: list[CrossrefIntegrityEntry] = []
        gaps: list[CrossrefIntegrityGap] = []
        for key, direction in (
            ("update-to", "update_to"),
            ("updated-by", "updated_by"),
        ):
            part_entries, part_gaps = cls._direction(item, key, direction)
            entries.extend(part_entries)
            gaps.extend(part_gaps)
        return tuple(entries), tuple(gaps)
