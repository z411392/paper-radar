import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.sqlite_harvest_coverage_adapter import (
    SqliteHarvestCoverageAdapter,
)
from libs.discovery.application.queries.read_harvest_coverage import ReadHarvestCoverage
from libs.discovery.exceptions.harvest_coverage_error import HarvestCoverageError
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.watch_profiles.application.queries.calibrate_domain_coverage import (
    CalibrateDomainCoverage,
)
from libs.watch_profiles.dtos.coverage_calibration import CoverageCalibrationCase


NOW = datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)
START = NOW - timedelta(days=1)


def _case(
    case_id: str,
    domain_id: str,
    expected: str,
    *,
    decision: str | None,
    execution_state: str = "succeeded",
    source_id: str = "crossref",
) -> CoverageCalibrationCase:
    return CoverageCalibrationCase(
        case_id=case_id,
        gold_set_version="gold:2026-09-v1",
        domain_id=domain_id,
        source_id=source_id,
        expected_label=expected,
        observed_decision=decision,
        execution_state=execution_state,
    )


def test_calibration_is_per_domain_and_exposes_badminton_false_positive() -> None:
    report = CalibrateDomainCoverage()(
        (
            _case("se:1", "software_engineering", "direct", decision="direct"),
            _case("dl:1", "deep_learning", "adjacent", decision="adjacent"),
            _case("ml:1", "machine_learning", "irrelevant", decision="irrelevant"),
            _case(
                "stats:1",
                "statistics",
                "direct",
                decision=None,
                execution_state="failed",
            ),
            _case(
                "badminton:direct",
                "badminton",
                "direct",
                decision="direct",
                source_id="pubmed",
            ),
            _case(
                "badminton:adjacent-tennis",
                "badminton",
                "adjacent",
                decision="direct",
                source_id="pubmed",
            ),
            _case(
                "badminton:reject-general-sport",
                "badminton",
                "irrelevant",
                decision="irrelevant",
                source_id="crossref",
            ),
            _case(
                "badminton:uncertain",
                "badminton",
                "direct",
                decision="uncertain",
                source_id="crossref",
            ),
        )
    )

    assert report.gold_set_version == "gold:2026-09-v1"
    assert report.population_scope == "not_enumerated"
    assert report.population_recall is None
    assert tuple(item.domain_id for item in report.domains) == (
        "badminton",
        "deep_learning",
        "machine_learning",
        "software_engineering",
        "statistics",
    )

    badminton = next(item for item in report.domains if item.domain_id == "badminton")
    assert badminton.sample_size == 4
    assert badminton.resolved == 3
    assert badminton.unresolved == 1
    assert badminton.correct == 2
    assert badminton.direct_expected == 2
    assert badminton.direct_correct == 1
    assert badminton.direct_false_positive == 1
    assert badminton.adjacent_expected == 1
    assert badminton.adjacent_correct == 0
    assert badminton.irrelevant_expected == 1
    assert badminton.irrelevant_correct == 1

    statistics = next(item for item in report.domains if item.domain_id == "statistics")
    assert statistics.unresolved == 1
    assert statistics.correct == 0


@pytest.mark.parametrize("execution_state", ["failed", "stale", "pending"])
def test_non_succeeded_relevance_is_unresolved_not_irrelevant(
    execution_state: str,
) -> None:
    report = CalibrateDomainCoverage()(
        (
            _case(
                "badminton:failure",
                "badminton",
                "irrelevant",
                decision=None,
                execution_state=execution_state,
            ),
        )
    )

    domain = report.domains[0]
    assert domain.irrelevant_expected == 1
    assert domain.irrelevant_correct == 0
    assert domain.unresolved == 1
    assert domain.resolved == 0


def _workspace(tmp_path: Path) -> SqliteSchemaConnectionFactory:
    migrations = load_workspace_migrations(with_runtime=True)
    root = tmp_path / "workspace"
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == len(migrations)
    return SqliteSchemaConnectionFactory(root, migrations)


def _payload(
    *,
    binding_key: str,
    domain_id: str,
    source_id: str,
) -> str:
    return json.dumps(
        {
            "binding_key": binding_key,
            "profile_id": "profile:coverage",
            "profile_revision": 3,
            "domain_id": domain_id,
            "domain_revision": 2,
            "source_id": source_id,
            "window_start": START.isoformat(),
            "window_end": NOW.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _insert_harvest_job(
    connection,
    *,
    job_id: str,
    binding_key: str,
    domain_id: str,
    source_id: str,
    state: str,
    error_code: str | None = None,
) -> None:
    input_json = _payload(
        binding_key=binding_key,
        domain_id=domain_id,
        source_id=source_id,
    )
    fingerprint = hashlib.sha256(input_json.encode()).hexdigest()
    connection.execute(
        "INSERT INTO workflow_jobs("
        "id,job_kind,business_key,input_json,input_fingerprint,state,due_at,"
        "lease_owner,lease_until,fencing_token,attempt_count,created_at"
        ") VALUES(?,?,?,?,?,?,?,NULL,NULL,0,?,?)",
        (
            job_id,
            "harvest_window",
            f"harvest:{job_id}",
            input_json,
            fingerprint,
            state,
            NOW.isoformat(),
            1 if error_code is not None else 0,
            START.isoformat(),
        ),
    )
    if error_code is not None:
        connection.execute(
            "INSERT INTO job_attempts("
            "id,job_id,attempt_no,fencing_token,state,error_code,started_at,finished_at"
            ") VALUES(?,?,1,1,?,?,?,?)",
            (
                f"attempt:{job_id}",
                job_id,
                state,
                error_code,
                START.isoformat(),
                NOW.isoformat(),
            ),
        )


def _insert_crossref_accounting(connection, *, binding_key: str) -> None:
    connection.execute(
        "INSERT INTO crossref_harvest_windows("
        "id,binding_key,query_fingerprint,config_version,from_index,until_index,"
        "rows,state,created_at,updated_at"
        ") VALUES(?,?,?,?,?,?,?,'repair_pending',?,?)",
        (
            "crossref-window:one",
            binding_key,
            "a" * 64,
            "p3-d2-fixture",
            START.isoformat(),
            NOW.isoformat(),
            1000,
            START.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO crossref_harvest_passes("
        "id,window_id,pass_no,parameters_fingerprint,state,current_cursor,next_page_no,"
        "traversal_complete,accounting_complete,first_reported_total,raw_item_count,"
        "processed_item_count,quarantine_count,unique_doi_count,duplicate_doi_count,"
        "parse_gap_count,drift_suspected,repair_pending,source_completeness,error_code,"
        "started_at,finished_at"
        ") VALUES(?,?,1,?,'completed',NULL,2,1,1,10,12,11,1,9,2,1,1,1,"
        "'provisional',NULL,?,?)",
        (
            "crossref-pass:one",
            "crossref-window:one",
            "b" * 64,
            START.isoformat(),
            NOW.isoformat(),
        ),
    )


def test_source_window_evidence_keeps_crossref_accounting_separate_from_recall(
    tmp_path: Path,
) -> None:
    schema = _workspace(tmp_path)
    connection = schema.connect()
    try:
        _insert_harvest_job(
            connection,
            job_id="job:badminton-pubmed",
            binding_key="binding:badminton:pubmed",
            domain_id="badminton",
            source_id="pubmed",
            state="succeeded",
        )
        _insert_harvest_job(
            connection,
            job_id="job:software-crossref",
            binding_key="binding:software:crossref",
            domain_id="software_engineering",
            source_id="crossref",
            state="failed",
            error_code="crossref_page_failed",
        )
        _insert_crossref_accounting(
            connection,
            binding_key="binding:software:crossref",
        )
        connection.commit()
    finally:
        connection.close()

    result = ReadHarvestCoverage(
        SqliteHarvestCoverageAdapter(schema.connect)
    )()

    assert len(result) == 2
    badminton = next(item for item in result if item.domain_id == "badminton")
    assert badminton.source_id == "pubmed"
    assert badminton.workflow_state == "succeeded"
    assert badminton.last_error_code is None
    assert badminton.crossref_generations == ()

    software = next(
        item for item in result if item.domain_id == "software_engineering"
    )
    assert software.workflow_state == "failed"
    assert software.last_error_code == "crossref_page_failed"
    assert software.population_recall is None
    assert len(software.crossref_generations) == 1

    generation = software.crossref_generations[0]
    assert generation.query_fingerprint == "a" * 64
    assert generation.window_state == "repair_pending"
    assert len(generation.passes) == 1
    pass_evidence = generation.passes[0]
    assert pass_evidence.traversal_complete is True
    assert pass_evidence.accounting_complete is True
    assert pass_evidence.first_reported_total == 10
    assert pass_evidence.raw_item_count == 12
    assert pass_evidence.unique_doi_count == 9
    assert pass_evidence.duplicate_doi_count == 2
    assert pass_evidence.quarantine_count == 1
    assert pass_evidence.parse_gap_count == 1
    assert pass_evidence.drift_suspected is True
    assert pass_evidence.repair_pending is True
    assert pass_evidence.source_completeness == "provisional"


def test_no_harvest_evidence_is_a_real_empty_collection(tmp_path: Path) -> None:
    schema = _workspace(tmp_path)

    assert ReadHarvestCoverage(SqliteHarvestCoverageAdapter(schema.connect))() == ()


def test_corrupt_harvest_job_payload_is_not_silently_dropped(tmp_path: Path) -> None:
    schema = _workspace(tmp_path)
    connection = schema.connect()
    try:
        connection.execute(
            "INSERT INTO workflow_jobs("
            "id,job_kind,business_key,input_json,input_fingerprint,state,due_at,"
            "lease_owner,lease_until,fencing_token,attempt_count,created_at"
            ") VALUES(?,?,?,?,?,'failed',?,NULL,NULL,0,0,?)",
            (
                "job:corrupt",
                "harvest_window",
                "harvest:corrupt",
                '{"domain_id":"badminton"}',
                "c" * 64,
                NOW.isoformat(),
                START.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(HarvestCoverageError, match="harvest_coverage_payload_corrupt"):
        ReadHarvestCoverage(SqliteHarvestCoverageAdapter(schema.connect))()
