from libs.watch_profiles.domain.services.domain_coverage_calibration_rules import (
    DomainCoverageCalibrationRules,
)
from libs.watch_profiles.dtos.coverage_calibration import (
    CoverageCalibrationCase,
    CoverageCalibrationReport,
)


class CalibrateDomainCoverage:
    def __call__(
        self,
        cases: tuple[CoverageCalibrationCase, ...],
    ) -> CoverageCalibrationReport:
        return DomainCoverageCalibrationRules.evaluate(cases)
