import json

from libs.scholarly_catalog.dtos.crossref_relation_assertion import (
    CrossrefRelationEntry,
    CrossrefRelationGap,
)


class CrossrefRelationRules:
    INTRA = frozenset(
        {
            "is-expression-of",
            "has-expression",
            "is-format-of",
            "has-format",
            "is-identical-to",
            "is-manifestation-of",
            "has-manifestation",
            "is-manuscript-of",
            "has-manuscript",
            "is-preprint-of",
            "has-preprint",
            "is-replaced-by",
            "replaces",
            "is-translation-of",
            "has-translation",
            "is-variant-form-of",
            "is-original-form-of",
            "is-version-of",
            "has-version",
        }
    )
    INTER = frozenset(
        {
            "is-based-on",
            "is-basis-for",
            "is-comment-on",
            "has-comment",
            "is-continued-by",
            "continues",
            "is-derived-from",
            "has-derivation",
            "is-documented-by",
            "documents",
            "finances",
            "is-financed-by",
            "is-part-of",
            "has-part",
            "is-review-of",
            "has-review",
            "references",
            "is-referenced-by",
            "is-related-material",
            "has-related-material",
            "is-reply-to",
            "has-reply",
            "requires",
            "is-required-by",
            "is-compiled-by",
            "compiles",
            "is-supplement-to",
            "is-supplemented-by",
        }
    )

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
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value.encode("utf-8", errors="ignore")) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            return None
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return None
        return value

    @classmethod
    def _classify(cls, predicate: str) -> str:
        lowered = predicate.lower()
        if lowered in cls.INTRA:
            return "intra_work"
        if lowered in cls.INTER:
            return "inter_work"
        return "unknown"

    @classmethod
    def extract(
        cls,
        item: dict[str, object],
    ) -> tuple[tuple[CrossrefRelationEntry, ...], tuple[CrossrefRelationGap, ...]]:
        relation = item.get("relation")
        if relation is None:
            return (), ()
        if not isinstance(relation, dict):
            return (), (
                CrossrefRelationGap(
                    "relation",
                    "relation_not_object",
                    cls._canonical(relation),
                ),
            )

        entries: list[CrossrefRelationEntry] = []
        gaps: list[CrossrefRelationGap] = []
        ordinal = 0
        for predicate_index, predicate in enumerate(sorted(relation)):
            values = relation[predicate]
            predicate_text = cls._text(predicate, 256)
            if predicate_text is None:
                gaps.append(
                    CrossrefRelationGap(
                        f"relation[predicate:{predicate_index}]",
                        "relation_predicate_invalid",
                        cls._canonical({predicate: values}),
                    )
                )
                continue
            if not isinstance(values, list):
                gaps.append(
                    CrossrefRelationGap(
                        "relation." + predicate_text,
                        "relation_entries_not_list",
                        cls._canonical(values),
                    )
                )
                continue
            for index, raw in enumerate(values):
                path = f"relation.{predicate_text}[{index}]"
                if not isinstance(raw, dict):
                    gaps.append(
                        CrossrefRelationGap(
                            path,
                            "relation_entry_not_object",
                            cls._canonical(raw),
                        )
                    )
                    continue
                id_type = cls._text(raw.get("id-type"), 128)
                target = cls._text(raw.get("id"), 4096)
                if id_type is None or target is None:
                    gaps.append(
                        CrossrefRelationGap(
                            path,
                            "relation_target_missing",
                            cls._canonical(raw),
                        )
                    )
                    continue
                asserted_raw = raw.get("asserted-by")
                if asserted_raw is None:
                    asserted = None
                else:
                    asserted = cls._text(asserted_raw, 128)
                    if asserted is None:
                        gaps.append(
                            CrossrefRelationGap(
                                path,
                                "relation_asserted_by_invalid",
                                cls._canonical(raw),
                            )
                        )
                        continue
                entries.append(
                    CrossrefRelationEntry(
                        ordinal,
                        predicate_text,
                        id_type,
                        target,
                        asserted,
                        cls._classify(predicate_text),
                    )
                )
                ordinal += 1
        return tuple(entries), tuple(gaps)
