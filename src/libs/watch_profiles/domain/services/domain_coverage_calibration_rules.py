from collections import defaultdict

from libs.watch_profiles.dtos.coverage_calibration import (
    CoverageCalibrationCase,
    CoverageCalibrationReport,
    DomainCoverageCalibration,
)
from libs.watch_profiles.exceptions.coverage_calibration_error import (
    CoverageCalibrationError,
)


class DomainCoverageCalibrationRules:
    _EXPECTED = frozenset({"direct", "adjacent", "irrelevant"})
    _DECISIONS = frozenset({"direct", "adjacent", "uncertain", "irrelevant"})
    _EXECUTION = frozenset({"pending", "succeeded", "failed", "stale"})

    @staticmethod
    def _text(value: object) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 256
            or "\0" in value
        ):
            raise CoverageCalibrationError("invalid_coverage_calibration")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise CoverageCalibrationError("invalid_coverage_calibration") from None
        return value

    @classmethod
    def evaluate(
        cls,
        cases: tuple[CoverageCalibrationCase, ...],
    ) -> CoverageCalibrationReport:
        if not isinstance(cases, tuple) or not cases:
            raise CoverageCalibrationError("invalid_coverage_calibration")

        versions: set[str] = set()
        case_ids: set[str] = set()
        by_domain: dict[str, list[CoverageCalibrationCase]] = defaultdict(list)

        for case in cases:
            if not isinstance(case, CoverageCalibrationCase):
                raise CoverageCalibrationError("invalid_coverage_calibration")
            case_id = cls._text(case.case_id)
            if case_id in case_ids:
                raise CoverageCalibrationError("duplicate_coverage_case")
            case_ids.add(case_id)
            versions.add(cls._text(case.gold_set_version))
            domain_id = cls._text(case.domain_id)
            cls._text(case.source_id)
            if case.expected_label not in cls._EXPECTED:
                raise CoverageCalibrationError("invalid_coverage_expected_label")
            if case.execution_state not in cls._EXECUTION:
                raise CoverageCalibrationError("invalid_coverage_execution_state")
            if (
                case.observed_decision is not None
                and case.observed_decision not in cls._DECISIONS
            ):
                raise CoverageCalibrationError("invalid_coverage_decision")
            if (
                case.execution_state == "succeeded"
                and case.observed_decision is None
            ):
                raise CoverageCalibrationError("invalid_coverage_decision")
            by_domain[domain_id].append(case)

        if len(versions) != 1:
            raise CoverageCalibrationError("mixed_gold_set_versions")
        version = next(iter(versions))

        domains: list[DomainCoverageCalibration] = []
        for domain_id in sorted(by_domain):
            rows = by_domain[domain_id]
            resolved = [
                row
                for row in rows
                if row.execution_state == "succeeded"
                and row.observed_decision in cls._EXPECTED
            ]
            direct_expected = sum(row.expected_label == "direct" for row in rows)
            adjacent_expected = sum(row.expected_label == "adjacent" for row in rows)
            irrelevant_expected = sum(row.expected_label == "irrelevant" for row in rows)
            domains.append(
                DomainCoverageCalibration(
                    domain_id=domain_id,
                    sample_size=len(rows),
                    resolved=len(resolved),
                    unresolved=len(rows) - len(resolved),
                    correct=sum(
                        row.observed_decision == row.expected_label
                        for row in resolved
                    ),
                    direct_expected=direct_expected,
                    direct_correct=sum(
                        row.expected_label == "direct"
                        and row.observed_decision == "direct"
                        for row in resolved
                    ),
                    direct_false_positive=sum(
                        row.expected_label != "direct"
                        and row.observed_decision == "direct"
                        for row in resolved
                    ),
                    adjacent_expected=adjacent_expected,
                    adjacent_correct=sum(
                        row.expected_label == "adjacent"
                        and row.observed_decision == "adjacent"
                        for row in resolved
                    ),
                    irrelevant_expected=irrelevant_expected,
                    irrelevant_correct=sum(
                        row.expected_label == "irrelevant"
                        and row.observed_decision == "irrelevant"
                        for row in resolved
                    ),
                )
            )

        return CoverageCalibrationReport(
            gold_set_version=version,
            population_scope="not_enumerated",
            population_recall=None,
            domains=tuple(domains),
        )
