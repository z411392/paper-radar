import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter import (
    SqliteWatchProfileStoreAdapter,
)
from libs.watch_profiles.application.commands.import_domain_seeds import ImportDomainSeeds
from libs.watch_profiles.application.commands.publish_watch_profile import PublishWatchProfile
from libs.watch_profiles.application.commands.select_watch_profile_revision import SelectWatchProfileRevision
from libs.watch_profiles.application.commands.set_watch_profile_lifecycle import SetWatchProfileLifecycle
from libs.watch_profiles.application.queries.read_watch_profile import ReadWatchProfile
from libs.watch_profiles.domain.services.normalize_watch_configuration import NormalizeWatchConfiguration
from libs.watch_profiles.exceptions.watch_configuration_error import WatchConfigurationError


@pytest.fixture
def normalizer():
    return NormalizeWatchConfiguration(frozenset({"arxiv", "pubmed", "crossref"}))


def domain(domain_id="machine_learning"):
    return {
        "id": domain_id,
        "name": "機器學習",
        "aliases": ["機器學習", "machine learning"],
        "include": ["machine learning"],
        "exclude": [],
        "sources": ["arxiv"],
        "source_categories": {"arxiv": ["cs.LG", "stat.ML"]},
    }


def profile(**changes):
    data = {
        "id": "personal",
        "reader_id": "local",
        "name": "研究閱讀",
        "scope_text": "關注統計方法",
        "domains": [{"id": "machine_learning", "revision": 1}],
        "filters": {
            "include": [],
            "exclude": [],
            "languages": ["en", "zh-Hant"],
            "sources": ["arxiv"],
            "free_only": True,
            "allow_preprints": True,
        },
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def store(tmp_path):
    root = Path(__file__).resolve().parents[5]
    database = tmp_path / "profiles.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in ["0001-object-registry.sql", "0002-watch-profiles.sql"]:
        connection.executescript((root / "migrations" / name).read_text(encoding="utf-8"))
    connection.commit()
    connection.close()

    def connect():
        con = sqlite3.connect(database, isolation_level=None, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    return SqliteWatchProfileStoreAdapter(connect), connect


def seed(store, normalizer):
    return ImportDomainSeeds(normalizer, store)(json.dumps({"domains": [domain()]}))


def test_five_domain_seed_configuration(normalizer):
    root = Path(__file__).resolve().parents[5]
    data = (root / "config/domain-seeds.json").read_text(encoding="utf-8")
    normalized = normalizer("domains", data)
    assert {d["id"] for d in json.loads(normalized.canonical_json)["domains"]} == {
        "software_engineering",
        "deep_learning",
        "machine_learning",
        "statistics",
        "badminton",
    }


def test_order_is_not_a_new_configuration(normalizer):
    first = json.loads(profile())
    second = json.loads(profile())
    second["filters"]["languages"].reverse()
    assert (
        normalizer("profile", json.dumps(first)).fingerprint
        == normalizer("profile", json.dumps(second)).fingerprint
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(unknown=True),
        lambda d: d.update(domains=[{"id": "machine_learning", "revision": True}]),
        lambda d: d["filters"].update(free_only="false"),
        lambda d: d["filters"].update(sources=["unregistered"]),
        lambda d: d.update(domains=d["domains"] * 2),
        lambda d: d.update(scope_text=""),
        lambda d: d["filters"].update(relevance_threshold=float("nan")),
    ],
)
def test_invalid_profile_is_rejected_before_io(normalizer, mutate):
    data = json.loads(profile())
    mutate(data)
    with pytest.raises(WatchConfigurationError):
        normalizer("profile", json.dumps(data))


def test_duplicate_json_keys_are_rejected(normalizer):
    with pytest.raises(WatchConfigurationError, match="duplicate_key"):
        normalizer("domains", '{"domains":[],"domains":[]}')


def test_invalid_batch_has_no_partial_domain_inserts(store, normalizer):
    adapter, connect = store
    bad = domain("badminton")
    bad["sources"] = ["unknown"]
    with pytest.raises(WatchConfigurationError):
        ImportDomainSeeds(normalizer, adapter)(json.dumps({"domains": [domain(), bad]}))
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM domain_definitions").fetchone()[0] == 0


def test_seed_replay_does_not_overwrite_existing_definition(store, normalizer):
    adapter, connect = store
    assert seed(adapter, normalizer)[0].disposition == "created"
    assert seed(adapter, normalizer)[0].disposition == "unchanged"
    changed = domain()
    changed["include"] = ["different topic"]
    result = ImportDomainSeeds(normalizer, adapter)(json.dumps({"domains": [changed]}))
    assert result[0].disposition == "preserved"
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM domain_definitions").fetchone()[0] == 1
        value = con.execute("SELECT definition_json FROM domain_definitions").fetchone()[0]
        assert json.loads(value)["include"] == ["machine learning"]


def test_atomic_publish_replay_and_no_stale_pointer_rollback(store, normalizer):
    adapter, connect = store
    seed(adapter, normalizer)
    publish = PublishWatchProfile(normalizer, adapter)
    v1 = publish(profile(), expected_revision=None)
    v2 = publish(profile(scope_text="關注因果推論"), expected_revision=1)
    late = publish(profile(), expected_revision=None)
    assert (v1.revision, v2.revision, late.revision) == (1, 2, 1)
    assert late.current_revision == 2
    assert ReadWatchProfile(adapter)("personal").revision == 2
    assert ReadWatchProfile(adapter)("personal", revision=1).scope_text == "關注統計方法"
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM watch_profile_revisions").fetchone()[0] == 2


def test_explicit_revision_selection_can_restore_historical_configuration(
    store,
    normalizer,
):
    adapter, _ = store
    seed(adapter, normalizer)
    publish = PublishWatchProfile(normalizer, adapter)
    v1 = publish(profile(), expected_revision=None)
    v2 = publish(
        profile(scope_text="關注因果推論"),
        expected_revision=1,
    )

    restored = SelectWatchProfileRevision(adapter)(
        "personal",
        v1.revision,
        expected_current_revision=v2.revision,
    )

    assert restored.revision == 1
    assert restored.current_revision == 1
    assert restored.scope_text == "關注統計方法"
    assert ReadWatchProfile(adapter)("personal").revision == 1


def test_explicit_revision_selection_uses_current_revision_cas(
    store,
    normalizer,
):
    adapter, _ = store
    seed(adapter, normalizer)
    publish = PublishWatchProfile(normalizer, adapter)
    publish(profile(), expected_revision=None)
    publish(
        profile(scope_text="關注因果推論"),
        expected_revision=1,
    )

    with pytest.raises(WatchConfigurationError, match="revision_conflict"):
        SelectWatchProfileRevision(adapter)(
            "personal",
            1,
            expected_current_revision=1,
        )

    assert ReadWatchProfile(adapter)("personal").revision == 2


def test_missing_domain_revision_rolls_back_everything(store, normalizer):
    adapter, connect = store
    seed(adapter, normalizer)
    with pytest.raises(WatchConfigurationError, match="domain_revision_missing"):
        PublishWatchProfile(normalizer, adapter)(
            profile(domains=[{"id": "machine_learning", "revision": 2}]), expected_revision=None
        )
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM watch_profiles").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM watch_profile_revisions").fetchone()[0] == 0


def test_stale_different_publication_is_rejected(store, normalizer):
    adapter, _ = store
    seed(adapter, normalizer)
    publish = PublishWatchProfile(normalizer, adapter)
    publish(profile(), expected_revision=None)
    publish(profile(scope_text="new"), expected_revision=1)
    with pytest.raises(WatchConfigurationError, match="revision_conflict"):
        publish(profile(scope_text="stale write"), expected_revision=1)
    with pytest.raises(WatchConfigurationError, match="identity_conflict"):
        publish(profile(reader_id="someone-else"), expected_revision=2)


def test_concurrent_identical_publish_creates_one_revision(store, normalizer):
    adapter, connect = store
    seed(adapter, normalizer)
    publish = PublishWatchProfile(normalizer, adapter)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: publish(profile(), expected_revision=None), range(8)))
    assert {r.revision for r in results} == {1}
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM watch_profile_revisions").fetchone()[0] == 1


def test_pause_publish_resume_and_domain_removal_preserve_history(store, normalizer):
    adapter, connect = store
    seed(adapter, normalizer)
    publish = PublishWatchProfile(normalizer, adapter)
    first = publish(profile(), expected_revision=None)
    SetWatchProfileLifecycle(adapter)("personal", "paused")
    second = publish(profile(domains=[], scope_text="暫不關注任何領域"), expected_revision=1)
    assert second.lifecycle == "paused"
    SetWatchProfileLifecycle(adapter)("personal", "active")
    reopened = SqliteWatchProfileStoreAdapter(connect)
    assert ReadWatchProfile(reopened)("personal").revision == 2
    assert ReadWatchProfile(reopened)("personal", revision=1).fingerprint == first.fingerprint
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM watch_profile_domains").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM watch_profile_revisions").fetchone()[0] == 2


def test_sql_failure_rolls_back_identity_revision_and_current_pointer(store, normalizer):
    adapter, connect = store
    seed(adapter, normalizer)
    with connect() as con:
        con.execute("""CREATE TRIGGER deny_profile_domain BEFORE INSERT ON watch_profile_domains
                       BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(WatchConfigurationError, match="storage_error"):
        PublishWatchProfile(normalizer, adapter)(profile(), expected_revision=None)
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM watch_profiles").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM watch_profile_revisions").fetchone()[0] == 0


def test_missing_query_is_explicit_and_does_not_create_profile(store):
    adapter, _ = store
    with pytest.raises(WatchConfigurationError, match="profile_missing"):
        ReadWatchProfile(adapter)("missing")


@pytest.mark.parametrize("payload", ["{", "[]", "null", '{"domains":NaN}'])
def test_invalid_json_or_document_shape(normalizer, payload):
    with pytest.raises(WatchConfigurationError):
        normalizer("domains", payload)


def test_domain_revision_binding_changes_fingerprint(normalizer):
    assert (
        normalizer("profile", profile()).fingerprint
        != normalizer("profile", profile(domains=[{"id": "machine_learning", "revision": 2}])).fingerprint
    )


def test_control_character_is_rejected(normalizer):
    with pytest.raises(WatchConfigurationError, match="invalid_text"):
        normalizer("profile", profile(scope_text="has\0nul"))


def test_two_different_writers_require_current_revision(store, normalizer):
    adapter, _ = store
    seed(adapter, normalizer)
    publish = PublishWatchProfile(normalizer, adapter)
    publish(profile(), expected_revision=None)

    def write(scope):
        try:
            return publish(profile(scope_text=scope), expected_revision=1).revision
        except WatchConfigurationError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["first competing edit", "second competing edit"]))
    assert results.count(2) == 1
    assert results.count("revision_conflict") == 1


def test_lifecycle_does_not_touch_real_reading_or_notification_history(store, normalizer):
    adapter, connect = store
    seed(adapter, normalizer)
    PublishWatchProfile(normalizer, adapter)(profile(), expected_revision=None)
    root = Path(__file__).resolve().parents[5]
    with connect() as con:
        for migration in sorted((root / "migrations").glob("*.sql"))[2:]:
            con.executescript(migration.read_text(encoding="utf-8"))
        con.execute("INSERT INTO paper_works VALUES('w','fixture','preprint',NULL,NULL,'t','t')")
        con.execute("INSERT INTO research_events VALUES('e','w',NULL,'new_work','event-key','{}',NULL,'t')")
        con.execute(
            "INSERT INTO delivery_subscriptions VALUES('s','local','email',0,'Asia/Taipei','{}',5,'r',1,'t')"
        )
        con.execute("INSERT INTO digests VALUES('d','s','period','t',NULL,'sent','t')")
        con.execute(
            "INSERT INTO delivery_outbox "
            "VALUES('o','d','delivery-key','digest','provider_accepted',1,NULL,'t')"
        )
        con.execute("INSERT INTO notification_ledger VALUES('n','local','e','email','o','accepted','t')")
        con.execute("INSERT INTO reader_feedback VALUES('f','local','w','read','personal','t')")
        tables = ("notification_ledger", "reader_feedback", "delivery_subscriptions", "delivery_outbox")
        before = {name: [tuple(row) for row in con.execute(f"SELECT * FROM {name}")] for name in tables}
    SetWatchProfileLifecycle(adapter)("personal", "paused")
    PublishWatchProfile(normalizer, adapter)(profile(domains=[]), expected_revision=1)
    SetWatchProfileLifecycle(adapter)("personal", "active")
    with connect() as con:
        after = {name: [tuple(row) for row in con.execute(f"SELECT * FROM {name}")] for name in tables}
        assert before == after
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_profile_reopens_in_another_process(store, normalizer):
    adapter, connect = store
    seed(adapter, normalizer)
    expected = PublishWatchProfile(normalizer, adapter)(profile(), expected_revision=None)
    with connect() as con:
        path = con.execute("PRAGMA database_list").fetchone()[2]
    code = """
import json, sqlite3, sys
from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter \
import SqliteWatchProfileStoreAdapter
from libs.watch_profiles.application.queries.read_watch_profile import ReadWatchProfile
def connect():
    con = sqlite3.connect(sys.argv[1], isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con
result = ReadWatchProfile(SqliteWatchProfileStoreAdapter(connect))('personal')
print(json.dumps({'revision':result.revision,'fingerprint':result.fingerprint}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, path],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"revision": 1, "fingerprint": expected.fingerprint}


def test_watch_profiles_use_the_actual_kernel_connection_factory(tmp_path, normalizer):
    from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
    from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
    from libs.kernel.dtos.migration import Migration

    root = Path(__file__).resolve().parents[5]
    migrations = tuple(
        Migration(i, name, (root / "migrations" / name).read_text(encoding="utf-8"))
        for i, name in [(1, "0001-object-registry.sql"), (2, "0002-watch-profiles.sql")]
    )
    workspace = tmp_path / "workspace"
    info = SqliteWorkspaceBootstrapAdapter(workspace, migrations).initialize()
    assert info.external_effects_enabled is False
    factory = SqliteConnectionFactory(workspace)
    adapter = SqliteWatchProfileStoreAdapter(factory.connect)
    seed(adapter, normalizer)
    result = PublishWatchProfile(normalizer, adapter)(profile(), expected_revision=None)
    assert result.revision == 1
    assert ReadWatchProfile(SqliteWatchProfileStoreAdapter(factory.connect))("personal") == result


@pytest.mark.parametrize("text", ["\ud800", "bad\udfff"])
def test_invalid_unicode_is_a_configuration_error(normalizer, text):
    with pytest.raises(WatchConfigurationError):
        normalizer("profile", profile(scope_text=text))


def test_out_of_range_domain_revision_is_rejected_before_sql(normalizer):
    with pytest.raises(WatchConfigurationError, match="invalid_domain_revision"):
        normalizer("profile", profile(domains=[{"id": "machine_learning", "revision": 2**63}]))
