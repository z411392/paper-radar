import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from apps.cli.adapters.driving.inspect_health import run_health_cli
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.watch_profiles.application.queries.calibrate_domain_coverage import (
    CalibrateDomainCoverage,
)
from libs.watch_profiles.dtos.coverage_calibration import CoverageCalibrationCase


NOW = datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)
START = NOW - timedelta(days=1)
PROFILE = "profile:operational-replay"
PROFILE_REVISION = 3
DOMAIN_REVISION = 7
GOLD_SET = "gold:operational-replay-v1"
BINDINGS = (
    ("software_engineering", "arxiv", "succeeded", None),
    ("deep_learning", "arxiv", "succeeded", None),
    ("machine_learning", "crossref", "failed", "crossref_unavailable"),
    ("statistics", "pubmed", "succeeded", None),
    ("badminton", "pubmed", "succeeded", None),
)


def _binding_key(domain_id: str, source_id: str) -> str:
    return (
        f"{PROFILE}:{PROFILE_REVISION}:{domain_id}:"
        f"{DOMAIN_REVISION}:{source_id}"
    )


def _job_payload(domain_id: str, source_id: str) -> str:
    return json.dumps(
        {
            "binding_key": _binding_key(domain_id, source_id),
            "profile_id": PROFILE,
            "profile_revision": PROFILE_REVISION,
            "domain_id": domain_id,
            "domain_revision": DOMAIN_REVISION,
            "source_id": source_id,
            "window_start": START.isoformat(),
            "window_end": NOW.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _seed_runtime_workspace(tmp_path):
    workspace = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        workspace,
        load_workspace_migrations(with_runtime=True),
    ).initialize()
    connection = SqliteConnectionFactory(workspace).connect()
    try:
        for domain_id, source_id, _, _ in BINDINGS:
            connection.execute(
                "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
                (
                    domain_id,
                    domain_id,
                    json.dumps({"sources": [source_id]}),
                    DOMAIN_REVISION,
                    START.isoformat(),
                ),
            )
        connection.execute(
            "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
            (
                PROFILE,
                "reader:local",
                "Operational replay",
                "active",
                PROFILE_REVISION,
                START.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
            (
                PROFILE,
                PROFILE_REVISION,
                "fixed replay",
                json.dumps({"sources": ["arxiv", "pubmed", "crossref"]}),
                "f" * 64,
                START.isoformat(),
            ),
        )
        for domain_id, _, _, _ in BINDINGS:
            connection.execute(
                "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
                (PROFILE, PROFILE_REVISION, domain_id, DOMAIN_REVISION),
            )

        for index, (domain_id, source_id, state, error_code) in enumerate(
            BINDINGS,
            start=1,
        ):
            payload = _job_payload(domain_id, source_id)
            fingerprint = hashlib.sha256(payload.encode()).hexdigest()
            job_id = f"job:operational-replay:{index}"
            business_key = f"harvest:operational-replay:{index}"
            connection.execute(
                "INSERT INTO workflow_jobs("
                "id,job_kind,business_key,input_json,input_fingerprint,state,due_at,"
                "fencing_token,attempt_count,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,1,?)",
                (
                    job_id,
                    "harvest_window",
                    business_key,
                    payload,
                    fingerprint,
                    state,
                    NOW.isoformat(),
                    index,
                    START.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO job_attempts("
                "id,job_id,attempt_no,fencing_token,state,error_code,started_at,finished_at"
                ") VALUES(?,?,1,?,?,?,?,?)",
                (
                    f"attempt:operational-replay:{index}",
                    job_id,
                    index,
                    state,
                    error_code,
                    (START + timedelta(minutes=index)).isoformat(),
                    (START + timedelta(minutes=index + 1)).isoformat(),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return workspace


def _case(
    case_id: str,
    domain_id: str,
    source_id: str,
    expected: str,
    observed: str | None,
    *,
    execution_state: str = "succeeded",
) -> CoverageCalibrationCase:
    return CoverageCalibrationCase(
        case_id=case_id,
        gold_set_version=GOLD_SET,
        domain_id=domain_id,
        source_id=source_id,
        expected_label=expected,
        observed_decision=observed,
        execution_state=execution_state,
    )


def test_fixed_five_domain_replay_keeps_health_and_sample_scope_separate(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _seed_runtime_workspace(tmp_path)

    run_health_cli(["health", "--workspace", str(workspace)])
    first = json.loads(capsys.readouterr().out)
    run_health_cli(["health", "--workspace", str(workspace)])
    second = json.loads(capsys.readouterr().out)

    assert first == second
    assert first["coverage_gaps"] == []
    assert len(first["sources"]) == 5
    by_domain = {row["domain_id"]: row for row in first["sources"]}
    assert by_domain["machine_learning"]["source_id"] == "crossref"
    assert by_domain["machine_learning"]["failure_count"] == 1
    assert by_domain["machine_learning"]["last_error_code"] == "crossref_unavailable"
    assert by_domain["badminton"]["source_id"] == "pubmed"
    assert by_domain["badminton"]["latest_successful_window_end"] == NOW.isoformat()

    calibration = CalibrateDomainCoverage()(
        (
            _case("se:direct", "software_engineering", "arxiv", "direct", "direct"),
            _case("dl:direct", "deep_learning", "arxiv", "direct", "direct"),
            _case("ml:failed", "machine_learning", "crossref", "direct", None,
                  execution_state="failed"),
            _case("stats:direct", "statistics", "pubmed", "direct", "direct"),
            _case("badminton:direct", "badminton", "pubmed", "direct", "direct"),
            _case(
                "badminton:adjacent-tennis",
                "badminton",
                "pubmed",
                "adjacent",
                "direct",
            ),
        )
    )

    assert calibration.gold_set_version == GOLD_SET
    assert calibration.population_scope == "not_enumerated"
    assert calibration.population_recall is None
    assert tuple(item.domain_id for item in calibration.domains) == (
        "badminton",
        "deep_learning",
        "machine_learning",
        "software_engineering",
        "statistics",
    )
    badminton = next(
        item for item in calibration.domains if item.domain_id == "badminton"
    )
    assert badminton.direct_false_positive == 1
    machine_learning = next(
        item for item in calibration.domains if item.domain_id == "machine_learning"
    )
    assert machine_learning.unresolved == 1
    assert machine_learning.correct == 0
