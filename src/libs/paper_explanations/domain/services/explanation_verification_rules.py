import hashlib
import json
import re
from collections import Counter

from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult, PaperClaim
from libs.paper_explanations.dtos.explanation_verification import (
    DeterministicVerificationReport,
    SupportStatement,
    SupportStatementVerdict,
    SupportVerificationCandidate,
    SupportVerificationRequest,
    SupportVerificationResult,
    VerificationFinding,
)
from libs.paper_explanations.dtos.reading_card_draft import ReadingCardDraft
from libs.paper_explanations.exceptions.explanation_verification_error import ExplanationVerificationError

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_UNIT_RE = re.compile(
    r"(?P<number>[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*"
    r"(?P<unit>%|(?:mhz|khz|hz|ms|mm|cm|km|kg|m|s|g)(?:/[A-Za-z0-9µμ°]+)?|"
    r"hours?|minutes?|seconds?|meters?|metres?|centimeters?|centimetres?|"
    r"millimeters?|millimetres?|kilometers?|kilometres?|kilograms?|grams?|"
    r"公尺|公分|毫米|公里|秒|毫秒|分鐘|小時|人|段|篇|次)"
    r"(?![A-Za-z0-9µμ°])",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|without|neither|nor)\b|"
    r"(?:沒有|並未|尚未|無法|無證據|無顯著|"
    r"未(?:觀察|發現|顯示|證明|驗證|提供|報告|達到|使用|包含|改善|提高|降低|支持|進行|完成)|"
    r"不(?:是|會|能|可|應|曾|再|顯著|增加|降低|改善|提高|支持|相關|存在|包含|代表|表示|等於|大於|小於))",
    re.IGNORECASE,
)
_CAUSAL_RE = re.compile(
    r"\b(?:cause|causes|caused|causing|lead to|leads to|led to|because|therefore)\b|"
    r"(?:導致|造成|因此|因為|使得)",
    re.IGNORECASE,
)
_HEDGE_RE = re.compile(
    r"\b(?:may|might|could|suggests?|associated with|association)\b|(?:可能|或許|推測)",
    re.IGNORECASE,
)
_STRONG_RE = re.compile(
    r"\b(?:prove|proves|proved|causes?|must)\b|(?:證明|必然|一定導致)",
    re.IGNORECASE,
)
_ROLE_PATTERNS = {
    "baseline": (r"baseline", r"基準", r"基线"),
    "proposed": (
        r"proposed(?: method)?",
        r"new method",
        r"our method",
        r"新方法",
        r"本方法",
        r"所提方法",
        r"提出的方法",
    ),
}


class ExplanationVerificationRules:
    @staticmethod
    def _canonical(value: object) -> str:
        try:
            text = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            text.encode("utf-8")
            return text
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise ExplanationVerificationError("invalid_verification_input") from exc

    @staticmethod
    def _numbers(text: str) -> Counter[str]:
        return Counter(match.group(0) for match in _NUMBER_RE.finditer(text))

    @staticmethod
    def _canonical_unit(value: str) -> str:
        unit = value.casefold()
        aliases = {
            "hour": "h", "hours": "h", "小時": "h",
            "minute": "min", "minutes": "min", "分鐘": "min",
            "second": "s", "seconds": "s", "秒": "s",
            "millisecond": "ms", "milliseconds": "ms", "毫秒": "ms",
            "meter": "m", "meters": "m", "metre": "m", "metres": "m", "公尺": "m",
            "centimeter": "cm", "centimeters": "cm", "centimetre": "cm", "centimetres": "cm", "公分": "cm",
            "millimeter": "mm", "millimeters": "mm", "millimetre": "mm", "millimetres": "mm", "毫米": "mm",
            "kilometer": "km", "kilometers": "km", "kilometre": "km", "kilometres": "km", "公里": "km",
            "kilogram": "kg", "kilograms": "kg",
            "gram": "g", "grams": "g",
        }
        return aliases.get(unit, unit)

    @classmethod
    def _unit_pairs(cls, text: str) -> Counter[tuple[str, str]]:
        return Counter(
            (m.group("number"), cls._canonical_unit(m.group("unit")))
            for m in _UNIT_RE.finditer(text)
        )

    @staticmethod
    def _is_subset(left: Counter, right: Counter) -> bool:
        return all(count <= right[item] for item, count in left.items())

    @staticmethod
    def _has_cjk(text: str) -> bool:
        return any("\u3400" <= char <= "\u9fff" for char in text)

    @staticmethod
    def _role_numbers(text: str) -> dict[str, set[str]]:
        found: dict[str, set[str]] = {"baseline": set(), "proposed": set()}
        number = r"([+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"
        for role, keywords in _ROLE_PATTERNS.items():
            for keyword in keywords:
                for match in re.finditer(rf"(?:{keyword})[^0-9]{{0,24}}{number}", text, re.IGNORECASE):
                    found[role].add(match.group(1))
                for match in re.finditer(rf"{number}[^A-Za-z0-9]{{0,16}}(?:{keyword})", text, re.IGNORECASE):
                    found[role].add(match.group(1))
        return found

    @classmethod
    def _linguistic_findings(
        cls,
        text: str,
        source: str,
        *,
        source_kind: str,
        source_index: int,
        claim_ids: tuple[str, ...] = (),
        require_all_numbers: bool = False,
    ) -> list[VerificationFinding]:
        findings: list[VerificationFinding] = []
        text_numbers = cls._numbers(text)
        source_numbers = cls._numbers(source)
        numbers_ok = (
            text_numbers == source_numbers
            if require_all_numbers
            else cls._is_subset(text_numbers, source_numbers)
        )
        if not numbers_ok:
            findings.append(VerificationFinding("number_mismatch", source_kind, source_index, claim_ids))

        if not cls._is_subset(cls._unit_pairs(text), cls._unit_pairs(source)):
            findings.append(VerificationFinding("unit_mismatch", source_kind, source_index, claim_ids))

        # Numeric proximity to role words is useful only when source and
        # output use the same writing system. English -> zh-TW translation can
        # legitimately reorder "baseline"/"proposed" around the same numbers.
        if cls._has_cjk(text) == cls._has_cjk(source):
            source_roles = cls._role_numbers(source)
            text_roles = cls._role_numbers(text)
            for role in ("baseline", "proposed"):
                if text_roles[role] and not text_roles[role] <= source_roles[role]:
                    findings.append(
                        VerificationFinding(
                            "role_value_mismatch",
                            source_kind,
                            source_index,
                            claim_ids,
                        )
                    )
                    break

        source_negated = bool(_NEGATION_RE.search(source))
        text_negated = bool(_NEGATION_RE.search(text))
        if source_negated and not text_negated:
            findings.append(VerificationFinding("negation_dropped", source_kind, source_index, claim_ids))
        elif text_negated and not source_negated:
            findings.append(VerificationFinding("negation_added", source_kind, source_index, claim_ids))

        if _CAUSAL_RE.search(text) and not _CAUSAL_RE.search(source):
            findings.append(VerificationFinding("causal_unsupported", source_kind, source_index, claim_ids))
        if _HEDGE_RE.search(source) and _STRONG_RE.search(text):
            findings.append(VerificationFinding("certainty_overstated", source_kind, source_index, claim_ids))
        return findings

    @classmethod
    def deterministic(
        cls,
        draft: ReadingCardDraft,
        claims: ClaimExtractionResult,
    ) -> DeterministicVerificationReport:
        if not isinstance(draft, ReadingCardDraft) or not isinstance(claims, ClaimExtractionResult):
            raise ExplanationVerificationError("invalid_verification_input")
        request = claims.request
        if (
            draft.snapshot_id != request.snapshot_id
            or draft.revision_id != request.revision_id
            or draft.work_id != request.work_id
            or draft.original_abstract != request.source_text
            or draft.evidence_level != "abstract_only"
            or draft.validation_state != "draft_requires_verification"
            or claims.validation_state != "anchor_bound_candidate"
        ):
            raise ExplanationVerificationError("verification_input_mismatch")
        if not isinstance(draft.faithful_translation, tuple) or not isinstance(
            draft.plain_language_card, tuple
        ):
            raise ExplanationVerificationError("invalid_verification_input")

        known_claims: dict[str, PaperClaim] = {}
        for claim in claims.claims:
            if not isinstance(claim, PaperClaim) or claim.claim_id in known_claims:
                raise ExplanationVerificationError("invalid_verification_input")
            known_claims[claim.claim_id] = claim
        anchors = {anchor.anchor_id: anchor for anchor in request.anchors}

        findings: list[VerificationFinding] = []
        if not draft.plain_language_card:
            findings.append(VerificationFinding("empty_reading_card", "card", -1, ()))

        translation_text = "\n".join(item.text for item in draft.faithful_translation)
        if cls._numbers(translation_text) != cls._numbers(draft.original_abstract):
            findings.append(VerificationFinding("number_mismatch", "translation", -1, ()))

        for index, passage in enumerate(draft.faithful_translation):
            if not isinstance(passage.anchor_ids, tuple) or not passage.anchor_ids:
                raise ExplanationVerificationError("invalid_verification_input")
            try:
                source = "\n".join(anchors[anchor_id].quote for anchor_id in passage.anchor_ids)
            except KeyError:
                raise ExplanationVerificationError("verification_anchor_mismatch") from None
            findings.extend(
                cls._linguistic_findings(
                    passage.text,
                    source,
                    source_kind="translation",
                    source_index=index,
                )
            )

        for index, statement in enumerate(draft.plain_language_card):
            if not isinstance(statement.claim_ids, tuple) or not statement.claim_ids:
                raise ExplanationVerificationError("invalid_verification_input")
            try:
                cited = tuple(known_claims[claim_id] for claim_id in statement.claim_ids)
            except KeyError:
                raise ExplanationVerificationError("verification_claim_mismatch") from None
            if any(claim.claim_type != statement.claim_type for claim in cited):
                raise ExplanationVerificationError("verification_claim_mismatch")
            source = "\n".join(quote for claim in cited for quote in claim.source_quotes)
            findings.extend(
                cls._linguistic_findings(
                    statement.text,
                    source,
                    source_kind="card",
                    source_index=index,
                    claim_ids=statement.claim_ids,
                )
            )

        unique: list[VerificationFinding] = []
        seen: set[tuple[str, str, int, tuple[str, ...]]] = set()
        for finding in findings:
            key = (finding.code, finding.source_kind, finding.source_index, finding.claim_ids)
            if key not in seen:
                seen.add(key)
                unique.append(finding)
        verdict = "rejected" if unique else "passed"
        return DeterministicVerificationReport(
            draft.snapshot_id,
            draft.revision_id,
            draft.work_id,
            draft.input_fingerprint,
            verdict,
            tuple(unique),
        )

    @classmethod
    def support_request(
        cls,
        draft: ReadingCardDraft,
        claims: ClaimExtractionResult,
        report: DeterministicVerificationReport,
    ) -> SupportVerificationRequest:
        if report.verdict != "passed" or report.findings:
            raise ExplanationVerificationError("deterministic_verification_required")
        known = {claim.claim_id: claim for claim in claims.claims}
        statements: list[SupportStatement] = []
        for index, statement in enumerate(draft.plain_language_card):
            try:
                cited = tuple(known[claim_id] for claim_id in statement.claim_ids)
            except KeyError:
                raise ExplanationVerificationError("verification_claim_mismatch") from None
            statements.append(
                SupportStatement(
                    index,
                    statement.claim_type,
                    statement.text,
                    statement.claim_ids,
                    tuple(quote for claim in cited for quote in claim.source_quotes),
                )
            )
        payload = {
            "format_version": 1,
            "snapshot_id": draft.snapshot_id,
            "draft_input_fingerprint": draft.input_fingerprint,
            "statements": [
                {
                    "statement_index": item.statement_index,
                    "claim_type": item.claim_type,
                    "text": item.text,
                    "claim_ids": item.claim_ids,
                    "source_quotes": item.source_quotes,
                }
                for item in statements
            ],
        }
        fingerprint = hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()
        return SupportVerificationRequest(draft.snapshot_id, fingerprint, tuple(statements))

    @classmethod
    def parse_support(
        cls,
        request: SupportVerificationRequest,
        candidate: SupportVerificationCandidate,
    ) -> SupportVerificationResult:
        if not isinstance(request, SupportVerificationRequest) or not isinstance(
            candidate, SupportVerificationCandidate
        ):
            raise ExplanationVerificationError("invalid_support_response")
        if (
            candidate.snapshot_id != request.snapshot_id
            or candidate.input_fingerprint != request.input_fingerprint
        ):
            raise ExplanationVerificationError("support_response_mismatch")
        expected = {statement.statement_index: statement for statement in request.statements}
        if len(candidate.statements) != len(expected):
            raise ExplanationVerificationError("support_response_mismatch")
        seen: set[int] = set()
        checked: list[SupportStatementVerdict] = []
        for item in candidate.statements:
            if (
                not isinstance(item, SupportStatementVerdict)
                or type(item.statement_index) is not int
                or item.statement_index in seen
                or item.statement_index not in expected
                or item.verdict not in {"supported", "unsupported", "uncertain"}
                or item.claim_ids != expected[item.statement_index].claim_ids
            ):
                raise ExplanationVerificationError("support_response_mismatch")
            seen.add(item.statement_index)
            checked.append(item)
        checked.sort(key=lambda item: item.statement_index)
        return SupportVerificationResult(request.snapshot_id, request.input_fingerprint, tuple(checked))
