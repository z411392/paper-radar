import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest

from libs.scholarly_catalog.adapters.driven.sqlite_evidence_snapshot_store_adapter import (
    SqliteEvidenceSnapshotStoreAdapter,
)
from libs.scholarly_catalog.domain.services.evidence_snapshot_rules import EvidenceSnapshotRules
from libs.scholarly_catalog.dtos.evidence_anchor_request import EvidenceAnchorRequest
from libs.scholarly_catalog.dtos.evidence_object_receipt import EvidenceObjectReceipt
from libs.scholarly_catalog.dtos.evidence_preparation_input import EvidencePreparationInput
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import EvidenceSnapshotError

ROOT = Path(__file__).resolve().parents[5]
AT = datetime(2026, 9, 23, tzinfo=timezone.utc)


@pytest.fixture
def storage(tmp_path):
    database = tmp_path / "evidence.sqlite3"

    def connect():
        connection = sqlite3.connect(database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    connection = connect()
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        for name in ("0001-object-registry.sql", "0003-scholarly-catalog.sql"):
            connection.executescript((ROOT / "migrations" / name).read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO paper_works(id,canonical_title,publication_status,first_seen_at,created_at) "
            "VALUES('work:fixture','Synthetic fixture','unknown',?,?)", (AT.isoformat(), AT.isoformat()),
        )
        connection.execute(
            "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
            ("manifestation:fixture", "work:fixture", "arxiv", "2609.00001", "preprint",
             "https://arxiv.org/abs/2609.00001", AT.isoformat()),
        )
        connection.execute(
            "INSERT INTO paper_revisions(id,manifestation_id,work_id,content_fingerprint,title,observed_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                "revision:fixture", "manifestation:fixture", "work:fixture",
                "a" * 64, "Synthetic", AT.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    def make(anchors=None):
        text = "摘要\n結果為42 units。"
        quote = "42 units"
        start = text.index(quote)
        value = EvidencePreparationInput(
            "revision:fixture", "work:fixture", b"synthetic source", "text/plain", "fixture-v1",
            text, "abstract", False, ("abstract",), (), None,
            anchors or (EvidenceAnchorRequest(quote, start, start + len(quote), "abstract", "p1"),), AT,
        )
        receipts = []
        connection = connect()
        try:
            for kind, data in (("evidence", value.source_bytes), ("extracted", text.encode())):
                digest = hashlib.sha256(data).hexdigest()
                object_id = f"{kind}:{digest}"
                connection.execute(
                    "INSERT OR IGNORE INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
                    (object_id, digest, f"objects/{kind}/{digest[:2]}/{digest}", kind, "text/plain",
                     len(data), "available", AT.isoformat(), "fixture"),
                )
                receipts.append(EvidenceObjectReceipt(object_id, digest))
            connection.commit()
        finally:
            connection.close()
        return EvidenceSnapshotRules.prepare(value, *receipts)

    return database, connect, SqliteEvidenceSnapshotStoreAdapter(connect), make


def count(connection, table):
    assert table in {"evidence_snapshots", "evidence_anchors", "object_registry"}
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_reopen_preserves_identity_created_at_and_additive_anchors(storage):
    _, connect, store, make = storage
    original = store.save(make())
    expanded = make((EvidenceAnchorRequest("摘要", 0, 2, "abstract", "p0"), *(
        EvidenceAnchorRequest(a.quote, a.offset_start, a.offset_end, a.section_label, a.paragraph_id)
        for a in original.anchors
    )))
    result = store.save(replace(expanded, created_at="2026-09-24T00:00:00+00:00"))
    assert result.snapshot_id == original.snapshot_id
    assert result.created_at == original.created_at
    assert len(result.anchors) == 2
    fresh = SqliteEvidenceSnapshotStoreAdapter(connect).read(original.snapshot_id)
    assert fresh == result
    assert store.save(make()) == result


@pytest.mark.parametrize("field,value", [
    ("parser_version", "tampered-v9"),
    ("created_at", "not-a-date"),
    ("coverage_json", "{}"),
    ("object_id", []),
], ids=["fingerprint", "time", "coverage", "object-type"])
def test_invalid_snapshot_is_rejected_before_insert(storage, field, value):
    _, connect, store, make = storage
    with pytest.raises(EvidenceSnapshotError):
        store.save(replace(make(), **{field: value}))
    connection = connect()
    try:
        assert count(connection, "evidence_snapshots") == 0
    finally:
        connection.close()


def test_forged_anchor_hash_is_rejected_before_insert(storage):
    _, _, store, make = storage
    snapshot = make()
    forged = replace(snapshot.anchors[0], anchor_id="anchor:" + "0" * 64)
    with pytest.raises(EvidenceSnapshotError, match="invalid_evidence_anchor"):
        store.save(replace(snapshot, anchors=(forged,)))


@pytest.mark.parametrize("sql", [
    "UPDATE evidence_snapshots SET coverage_json='{}'",
    "UPDATE evidence_anchors SET id='anchor:' || lower(hex(zeroblob(32)))",
    "UPDATE object_registry SET state='quarantined' WHERE kind='extracted'",
    "UPDATE object_registry SET content_sha256=lower(hex(zeroblob(32))) WHERE kind='extracted'",
], ids=["coverage-corruption", "anchor-corruption", "object-quarantined", "registry-hash-corruption"])
def test_readback_rejects_corruption_or_unavailable_object(storage, sql):
    _, connect, store, make = storage
    snapshot = store.save(make())
    connection = connect()
    try:
        connection.execute(sql)
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(EvidenceSnapshotError):
        store.read(snapshot.snapshot_id)


def test_anchor_failure_rolls_back_snapshot_and_prior_anchor(storage):
    _, connect, store, make = storage
    snapshot = make((EvidenceAnchorRequest("摘要", 0, 2, "abstract"),
                     EvidenceAnchorRequest("42 units", 6, 14, "abstract")))
    connection = connect()
    try:
        connection.executescript(
            "CREATE TRIGGER fail_second BEFORE INSERT ON evidence_anchors WHEN NEW.offset_start > 0 "
            "BEGIN SELECT RAISE(ABORT, 'injected fixture failure'); END;"
        )
    finally:
        connection.close()
    with pytest.raises(EvidenceSnapshotError, match="evidence_database_conflict"):
        store.save(snapshot)
    connection = connect()
    try:
        assert count(connection, "evidence_snapshots") == count(connection, "evidence_anchors") == 0
        assert count(connection, "object_registry") == 2
        connection.execute("DROP TRIGGER fail_second")
        connection.commit()
    finally:
        connection.close()
    assert len(store.save(snapshot).anchors) == 2


def test_two_writers_produce_one_snapshot_and_one_anchor(storage):
    _, connect, store, make = storage
    snapshot = make()
    barrier = Barrier(2)

    def save():
        barrier.wait(timeout=5)
        return SqliteEvidenceSnapshotStoreAdapter(connect).save(snapshot)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(lambda _: save(), range(2)))
    assert first == second
    connection = connect()
    try:
        assert count(connection, "evidence_snapshots") == count(connection, "evidence_anchors") == 1
    finally:
        connection.close()


def test_process_exit_during_anchor_insert_is_recoverable(storage, tmp_path):
    database, connect, store, make = storage
    snapshot = make()
    payload = tmp_path / "snapshot.json"
    payload.write_text(json.dumps(asdict(snapshot)), encoding="utf-8")
    code = """
import json, os, sqlite3, sys
from libs.scholarly_catalog.adapters.driven.sqlite_evidence_snapshot_store_adapter import (
    SqliteEvidenceSnapshotStoreAdapter,
)
from libs.scholarly_catalog.dtos.evidence_anchor import EvidenceAnchor
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot

def connect():
    c = sqlite3.connect(sys.argv[1])
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.create_function('kill_process', 0, lambda: os._exit(73))
    return c

c = connect()
c.executescript('CREATE TRIGGER crash AFTER INSERT ON evidence_anchors BEGIN SELECT kill_process(); END;')
c.close()
with open(sys.argv[2], encoding='utf-8') as f:
    value = json.load(f)
value['anchors'] = tuple(EvidenceAnchor(**a) for a in value['anchors'])
SqliteEvidenceSnapshotStoreAdapter(connect).save(EvidenceSnapshot(**value))
"""
    result = subprocess.run([sys.executable, "-c", code, str(database), str(payload)],
                            cwd=ROOT / "src", capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 73, result.stdout + result.stderr
    connection = connect()
    try:
        assert count(connection, "evidence_snapshots") == count(connection, "evidence_anchors") == 0
        assert count(connection, "object_registry") == 2
        connection.execute("DROP TRIGGER crash")
        connection.commit()
    finally:
        connection.close()
    assert store.save(snapshot).snapshot_id == snapshot.snapshot_id
    assert SqliteEvidenceSnapshotStoreAdapter(connect).read(snapshot.snapshot_id) == snapshot
