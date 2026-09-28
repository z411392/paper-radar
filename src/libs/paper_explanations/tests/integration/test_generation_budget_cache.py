import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from libs.paper_explanations.adapters.driven.sqlite_generation_ledger_adapter import SqliteGenerationLedgerAdapter
from libs.paper_explanations.application.commands.run_budgeted_generation import RunBudgetedGeneration
from libs.paper_explanations.domain.services.generation_execution_rules import GenerationExecutionRules
from libs.paper_explanations.dtos.generation_budget_policy import GenerationBudgetPolicy
from libs.paper_explanations.dtos.structured_generation_request import MODEL_NAME, StructuredGenerationRequest
from libs.paper_explanations.dtos.structured_generation_result import GenerationReceipt, StructuredGenerationResult
from libs.paper_explanations.exceptions.generation_ledger_error import GenerationLedgerError
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError

ROOT = Path(__file__).resolve().parents[5]
AT = datetime(2026, 9, 23, tzinfo=timezone.utc)


class FileObjects:
    def __init__(self, root: Path, db: Path) -> None:
        self.root, self.db = root, db
        root.mkdir()

    def publish(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        (self.root / digest).write_bytes(content)
        object_id = f"model_output:{digest}"
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute(
                "INSERT OR IGNORE INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
                (object_id, digest, f"objects/model_output/{digest[:2]}/{digest}", "model_output",
                 "application/json; charset=utf-8", len(content), "available", AT.isoformat(),
                 "generation-output-v1"),
            )
        return object_id

    def read(self, object_id: str) -> bytes:
        return (self.root / object_id.split(":", 1)[1]).read_bytes()


class Clock:
    def __init__(self, value: datetime = AT) -> None:
        self.value = value

    def __call__(self) -> datetime:
        value, self.value = self.value, self.value + timedelta(seconds=1)
        return value


def setup(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "app.sqlite3"
    with sqlite3.connect(db) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript((ROOT / "migrations/0001-object-registry.sql").read_text())
        c.executescript((ROOT / "migrations/0005-paper-explanations.sql").read_text())

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(db, isolation_level=None, timeout=1)

    objects = FileObjects(tmp_path / "objects", db)
    return db, objects, connect, SqliteGenerationLedgerAdapter(connect, objects)


def request(**changes):
    return replace(
        StructuredGenerationRequest(
            "abstract_reading_card", "a" * 64, "Translate faithfully.",
            '{"text":"180 clips; 0.42 m versus 0.58 m"}', "reading_card",
            '{"type":"object","additionalProperties":false,"properties":{}}',
        ),
        **changes,
    )


def policy(**changes):
    return replace(GenerationBudgetPolicy("2026-09-23", "USD", 2_000, 600, "b" * 64), **changes)


def receipt(**changes):
    return replace(
        GenerationReceipt("a" * 64, "c" * 64, "gen-1", MODEL_NAME, MODEL_NAME,
                          "Google AI Studio", 282, 87, "0.0005323725", "stop"),
        **changes,
    )


def result(**changes):
    return StructuredGenerationResult('{"ok":true}', receipt(**changes))


def rows(db: Path, table: str):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return [dict(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY id")]


def test_success_cache_reopens_without_new_provider_or_budget(tmp_path: Path) -> None:
    db, objects, connect, ledger = setup(tmp_path)
    model = Mock(return_value=result())
    first = RunBudgetedGeneration(model, ledger, Clock(), policy())(request())
    second = RunBudgetedGeneration(
        model, SqliteGenerationLedgerAdapter(connect, objects), Clock(AT + timedelta(minutes=1)), policy()
    )(request())
    assert first == second and model.call_count == 1
    run, usage = rows(db, "model_runs")[0], rows(db, "usage_reservations")[0]
    assert run["state"] == "succeeded" and run["actual_cost_micros"] == 533
    assert usage["state"] == "settled" and usage["actual_micros"] == 533
    saved = objects.read(run["output_object_id"]).decode()
    assert '"cost_usd":"0.0005323725"' in saved and '"provider":"Google AI Studio"' in saved


def test_cache_identity_changes_with_prompt_payload_or_execution_policy(tmp_path: Path) -> None:
    _, _, _, _ = setup(tmp_path)
    values = {
        GenerationExecutionRules.identity(request(), policy()).generation_fingerprint,
        GenerationExecutionRules.identity(request(system_prompt="Different"), policy()).generation_fingerprint,
        GenerationExecutionRules.identity(request(payload_json='{"different":true}'), policy()).generation_fingerprint,
        GenerationExecutionRules.identity(request(), policy(execution_policy_fingerprint="d" * 64)).generation_fingerprint,
    }
    assert len(values) == 4


def test_budget_block_and_same_identity_in_progress_never_call_provider(tmp_path: Path) -> None:
    db, _, _, ledger = setup(tmp_path)
    model = Mock(return_value=result())
    RunBudgetedGeneration(model, ledger, Clock(), policy(period_limit_micros=1_000))(request())
    with pytest.raises(ModelGatewayError, match="budget_blocked") as raised:
        RunBudgetedGeneration(
            model,
            ledger,
            Clock(AT + timedelta(minutes=1)),
            policy(period_limit_micros=1_000),
        )(request(payload_json='{"other":true}'))
    blocked = [row for row in rows(db, "model_runs") if row["state"] == "budget_blocked"][0]
    assert raised.value.run_id == blocked["id"]
    assert raised.value.generation_fingerprint == blocked["input_fingerprint"]
    active = GenerationExecutionRules.identity(request(payload_json='{"active":true}'), policy())
    ledger.reserve(active, AT + timedelta(minutes=2))
    before = model.call_count
    with pytest.raises(GenerationLedgerError, match="generation_in_progress"):
        RunBudgetedGeneration(model, ledger, Clock(AT + timedelta(minutes=3)), policy())(
            request(payload_json='{"active":true}')
        )
    assert model.call_count == before
    assert "budget_blocked" in {row["state"] for row in rows(db, "model_runs")}


def test_actual_cost_above_reservation_fails_closed_and_is_accounted(
    tmp_path: Path,
) -> None:
    db, _, _, ledger = setup(tmp_path)
    model = Mock(
        return_value=result(
            cost_usd="0.000700",
        )
    )

    with pytest.raises(
        ModelGatewayError,
        match="cost_exceeded_reservation",
    ):
        RunBudgetedGeneration(
            model,
            ledger,
            Clock(),
            policy(
                period_limit_micros=1_000,
                reservation_micros=600,
            ),
        )(request())

    run = rows(db, "model_runs")[0]
    usage = rows(db, "usage_reservations")[0]
    assert run["state"] == "failed"
    assert run["actual_cost_micros"] == 700
    assert run["error_code"] == "cost_exceeded_reservation"
    assert usage["state"] == "settled"
    assert usage["actual_micros"] == 700

    next_model = Mock(return_value=result(generation_id="gen-2"))
    with pytest.raises(ModelGatewayError, match="budget_blocked"):
        RunBudgetedGeneration(
            next_model,
            ledger,
            Clock(AT + timedelta(minutes=1)),
            policy(
                period_limit_micros=1_000,
                reservation_micros=600,
            ),
        )(request(payload_json='{"next":true}'))
    next_model.assert_not_called()


def test_failed_known_cost_is_not_cache_but_exact_receipt_is_preserved(tmp_path: Path) -> None:
    db, objects, _, ledger = setup(tmp_path)
    failed = receipt(cost_usd="0.0001000001", finish_reason=None)
    with pytest.raises(ModelGatewayError, match="provider_unavailable") as raised:
        RunBudgetedGeneration(
            Mock(side_effect=ModelGatewayError("provider_unavailable", failed)),
            ledger,
            Clock(),
            policy(),
        )(request())
    run = rows(db, "model_runs")[0]
    assert raised.value.run_id == run["id"]
    assert raised.value.generation_fingerprint == run["input_fingerprint"]
    assert raised.value.receipt == failed
    assert run["state"] == "failed" and run["actual_cost_micros"] == 101
    assert rows(db, "usage_reservations")[0]["state"] == "settled"
    assert '"cost_usd":"0.0001000001"' in objects.read(run["output_object_id"]).decode()
    model = Mock(return_value=result(generation_id="gen-2"))
    RunBudgetedGeneration(model, ledger, Clock(AT + timedelta(minutes=1)), policy())(request())
    assert model.call_count == 1


def test_unknown_charge_consumes_reserved_budget_but_local_preflight_releases(tmp_path: Path) -> None:
    db, _, _, ledger = setup(tmp_path)
    with pytest.raises(ModelGatewayError, match="timeout"):
        RunBudgetedGeneration(Mock(side_effect=ModelGatewayError("timeout")), ledger, Clock(),
                              policy(period_limit_micros=1_000))(request())
    assert rows(db, "usage_reservations")[0]["state"] == "unknown"
    model = Mock(return_value=result())
    with pytest.raises(ModelGatewayError, match="budget_blocked"):
        RunBudgetedGeneration(model, ledger, Clock(AT + timedelta(minutes=1)),
                              policy(period_limit_micros=1_000))(request(payload_json='{"next":true}'))
    model.assert_not_called()

    db2, _, _, ledger2 = setup(tmp_path / "local")
    with pytest.raises(ModelGatewayError, match="invalid_generation_request"):
        RunBudgetedGeneration(Mock(side_effect=ModelGatewayError("invalid_generation_request")),
                              ledger2, Clock(), policy(period_limit_micros=600))(request())
    assert rows(db2, "usage_reservations")[0]["state"] == "released"


def test_stale_reconciliation_keeps_reserved_and_running_charge_unknown(tmp_path: Path) -> None:
    db, _, _, ledger = setup(tmp_path)
    one = GenerationExecutionRules.identity(request(payload_json='{"one":1}'), policy())
    two = GenerationExecutionRules.identity(request(payload_json='{"two":2}'), policy())
    first, second = ledger.reserve(one, AT), ledger.reserve(two, AT)
    ledger.mark_running(second.run_id, two)
    assert ledger.reconcile_stale(AT + timedelta(seconds=1), AT + timedelta(minutes=1)) == (0, 2)
    state = {row["run_id"]: row["state"] for row in rows(db, "usage_reservations")}
    assert state[first.run_id] == state[second.run_id] == "unknown"


def test_invalid_model_result_becomes_failed_unknown_charge(tmp_path: Path) -> None:
    db, _, _, ledger = setup(tmp_path)
    with pytest.raises(GenerationLedgerError, match="invalid_generation_result"):
        RunBudgetedGeneration(Mock(return_value={"bad": True}), ledger, Clock(), policy())(request())
    assert rows(db, "model_runs")[0]["state"] == "failed"
    assert rows(db, "usage_reservations")[0]["state"] == "unknown"


def test_cached_content_and_cost_boundaries_are_fail_closed(tmp_path: Path) -> None:
    _, _, _, ledger = setup(tmp_path)
    identity = GenerationExecutionRules.identity(request(), policy())
    reservation = ledger.reserve(identity, AT)
    ledger.mark_running(reservation.run_id, identity)
    for content in ('{"x":1,"x":2}', '{"x":NaN}'):
        with pytest.raises(GenerationLedgerError, match="invalid_generation_result"):
            ledger.complete_success(reservation.run_id, identity,
                                    StructuredGenerationResult(content, receipt()), AT + timedelta(seconds=1))
    assert GenerationExecutionRules.cost_micros("0.0005323725") == 533
    for value in ("1e1000000", "NaN", "-1", "1000001", "0.0000000000000000001"):
        with pytest.raises(GenerationLedgerError, match="invalid_generation_receipt"):
            GenerationExecutionRules.cost_micros(value)


def test_concurrent_same_identity_reservation_creates_one_live_attempt(tmp_path: Path) -> None:
    db, objects, connect, ledger = setup(tmp_path)
    identity = GenerationExecutionRules.identity(request(), policy())

    def reserve(_):
        return SqliteGenerationLedgerAdapter(connect, objects).reserve(identity, AT).state

    with ThreadPoolExecutor(max_workers=6) as pool:
        states = list(pool.map(reserve, range(6)))
    assert states.count("reserved") == 1 and states.count("in_progress") == 5
    assert len(rows(db, "model_runs")) == len(rows(db, "usage_reservations")) == 1


def test_cached_object_is_bound_to_full_generation_identity(tmp_path: Path) -> None:
    _, objects, connect, ledger = setup(tmp_path)
    first_identity = GenerationExecutionRules.identity(request(), policy())
    reserved = ledger.reserve(first_identity, AT)
    ledger.mark_running(reserved.run_id, first_identity)
    ledger.complete_success(reserved.run_id, first_identity, result(), AT + timedelta(seconds=1))
    cached = ledger.reserve(first_identity, AT + timedelta(minutes=1))
    assert cached.state == "cached" and cached.output_object_id is not None

    other_identity = GenerationExecutionRules.identity(
        request(),
        policy(execution_policy_fingerprint="d" * 64),
    )
    with pytest.raises(GenerationLedgerError, match="invalid_cached_generation"):
        SqliteGenerationLedgerAdapter(connect, objects).read_cached(
            cached.output_object_id,
            other_identity,
        )


def test_tracked_execution_exposes_local_run_and_generation_identity(tmp_path: Path) -> None:
    _, _, _, ledger = setup(tmp_path)
    model = Mock(return_value=result())
    command = RunBudgetedGeneration(model, ledger, Clock(), policy())

    execution = command.execute(request())

    identity = GenerationExecutionRules.identity(request(), policy())
    assert execution.run_id.startswith("run:")
    assert execution.generation_fingerprint == identity.generation_fingerprint
    assert execution.result == result()
    assert execution.cached is False


def test_tracked_cache_replay_returns_same_local_run_identity(tmp_path: Path) -> None:
    _, objects, connect, ledger = setup(tmp_path)
    model = Mock(return_value=result())
    first = RunBudgetedGeneration(model, ledger, Clock(), policy()).execute(request())
    second = RunBudgetedGeneration(
        model,
        SqliteGenerationLedgerAdapter(connect, objects),
        Clock(AT + timedelta(minutes=1)),
        policy(),
    ).execute(request())

    assert second.cached is True
    assert second.run_id == first.run_id
    assert second.generation_fingerprint == first.generation_fingerprint
    assert model.call_count == 1
