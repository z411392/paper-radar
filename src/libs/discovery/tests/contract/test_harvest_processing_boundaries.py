import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from libs.discovery.adapters.driven.sqlite_harvest_processing_adapter import SqliteHarvestProcessingAdapter
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.tests.contract.test_harvest_page_processing import (
    AT,
    VERSION,
    database,
    metadata,
    process,
    saved,
    state,
)
from libs.discovery.tests.contract.test_harvest_page_processing import env as env


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("field,value", [("complete", 0), ("format_version", True), ("record_count", 2.0)])
def test_equal_python_values_do_not_hide_wrong_persisted_types(env, field, value):
    saved(env)
    process(env)("page-a", 0, AT)
    coverage = json.loads(state(env)[3])
    coverage[field] = value
    database(env, "UPDATE harvest_units SET coverage_json=?", (canonical(coverage),))
    with pytest.raises(HarvestError):
        process(env)("page-a", 0, AT)


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"expected_checkpoint_version": 1, "checkpoint_version": 2}, 1),
        ({"observation_ids": ["observation:" + "f" * 64]}, 0),
        ({"next_start": 0, "total_results": None}, 0),
    ],
)
def test_receipt_must_agree_with_actual_committed_observations(env, change, expected):
    saved(env)
    process(env)("page-a", 0, AT)
    document = metadata(env)
    document["processing"]["harvest-page-v1/" + VERSION].update(change)
    database(env, "UPDATE harvest_attempts SET response_metadata_json=?", (canonical(document),))
    with pytest.raises(HarvestError):
        process(env)("page-a", expected, AT)


def test_failed_unit_cannot_claim_preexisting_successful_progress(env):
    saved(env)
    process(env)("page-a", 0, AT)
    database(env, "UPDATE harvest_units SET state='failed'")
    with pytest.raises(HarvestError):
        process(env)("page-a", 0, AT)


def test_two_attempts_for_same_page_have_one_winner(env):
    saved(env, "first")
    saved(env, "second")
    barrier = Barrier(2)

    class Synchronized:
        def snapshot(self, attempt, parser_version):
            value = env.store.snapshot(attempt, parser_version)
            barrier.wait(timeout=10)
            return value

        def commit(self, *args):
            return env.store.commit(*args)

    def run(identity):
        try:
            return process(env, store=Synchronized())(identity, 0, AT).state
        except HarvestError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("first", "second")))
    assert sorted(results) == ["checkpoint_conflict", "partial"]
    assert state(env)[1] == 1 and len(database(env, "SELECT * FROM source_observations")) == 2


def test_recorded_parse_failure_replays_without_running_parser(env):
    saved(env, body=b"<bad")
    first = process(env)("page-a", 0, AT)
    parser = Mock()
    assert process(env, parser=parser)("page-a", 0, AT) == first
    parser.assert_not_called()


def test_disabled_binding_is_rechecked_inside_write_transaction(env):
    saved(env)

    class Disabled:
        def snapshot(self, attempt, parser_version):
            return env.store.snapshot(attempt, parser_version)

        def commit(self, *args):
            database(env, "UPDATE source_bindings SET enabled=0")
            return env.store.commit(*args)

    with pytest.raises(HarvestError, match="binding_conflict"):
        process(env, store=Disabled())("page-a", 0, AT)
    assert state(env)[1] == 0 and database(env, "SELECT * FROM source_observations") == []


def test_missing_foreign_key_enforcement_cannot_commit(env):
    attempt, _ = saved(env)

    def connect():
        return sqlite3.connect(env.root / "state/app.sqlite3", isolation_level=None)

    with pytest.raises(HarvestError, match="foreign_keys_required"):
        SqliteHarvestProcessingAdapter(connect).snapshot(attempt, VERSION)


def test_process_death_between_observations_and_checkpoint_rolls_back(env):
    saved(env)
    database(
        env,
        "CREATE TRIGGER crash_processing BEFORE UPDATE ON harvest_units BEGIN SELECT crash_fixture(); END",
    )
    code = """
import os, sys
from datetime import datetime, timezone
from pathlib import Path
from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import ArxivAtomParserAdapter
from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.adapters.driven.sqlite_harvest_processing_adapter import SqliteHarvestProcessingAdapter
from libs.discovery.adapters.driven.sqlite_harvest_store_adapter import SqliteHarvestStoreAdapter
from libs.discovery.application.commands.process_harvest_page import ProcessHarvestPage
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
from libs.discovery.application.queries.parse_source_page import ParseSourcePage
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.application.queries.read_object import ReadObject
root = Path(sys.argv[1])
factory = SqliteConnectionFactory(root)
def connect():
    connection = factory.connect()
    connection.create_function("crash_fixture", 0, lambda: os._exit(23))
    return connection
journal = SqliteHarvestStoreAdapter(factory.connect, ArxivQueryCompilerAdapter())
objects = ReadObject(FilesystemObjectBytesAdapter(root), SqliteObjectUnitOfWorkAdapter(factory))
command = ProcessHarvestPage(ReadHarvestAttempt(journal), objects,
    ParseSourcePage(ArxivAtomParserAdapter()), SqliteHarvestProcessingAdapter(connect), "arxiv-atom-v1")
command("page-a", 0, datetime(2026, 9, 22, 0, 0, 5, tzinfo=timezone.utc))
raise AssertionError("the fixture trigger must terminate this process")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(env.root)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 23, result.stderr
    assert state(env)[1:] == (0, None, "{}")
    assert database(env, "SELECT * FROM source_observations") == []
    assert "processing" not in metadata(env)
    database(env, "DROP TRIGGER crash_processing")
    assert process(env)("page-a", 0, AT).checkpoint_version == 1
    assert len(database(env, "SELECT * FROM object_registry")) == 1
