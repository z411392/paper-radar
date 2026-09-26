from dataclasses import dataclass


@dataclass(frozen=True)
class CoverageCalibrationCase:
    case_id: str
    gold_set_version: str
    domain_id: str
    source_id: str
    expected_label: str
    observed_decision: str | None
    execution_state: str


@dataclass(frozen=True)
class DomainCoverageCalibration:
    domain_id: str
    sample_size: int
    resolved: int
    unresolved: int
    correct: int
    direct_expected: int
    direct_correct: int
    direct_false_positive: int
    adjacent_expected: int
    adjacent_correct: int
    irrelevant_expected: int
    irrelevant_correct: int


@dataclass(frozen=True)
class CoverageCalibrationReport:
    gold_set_version: str
    population_scope: str
    population_recall: None
    domains: tuple[DomainCoverageCalibration, ...]
