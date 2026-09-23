import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject
from libs.kernel.exceptions.storage_error import StorageError
from libs.scholarly_catalog.adapters.driven.kernel_evidence_object_adapter import KernelEvidenceObjectAdapter
from libs.scholarly_catalog.adapters.driven.safe_access_evidence_policy_adapter import (
    SafeAccessEvidencePolicyAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_access_assessment_store_adapter import (
    SqliteAccessAssessmentStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_evidence_snapshot_store_adapter import (
    SqliteEvidenceSnapshotStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_paper_identity_store_adapter import (
    SqlitePaperIdentityStoreAdapter,
)
from libs.scholarly_catalog.application.commands.prepare_evidence_snapshot import PrepareEvidenceSnapshot
from libs.scholarly_catalog.application.commands.resolve_paper_identity import ResolvePaperIdentity
from libs.scholarly_catalog.application.commands.verify_readable_location import VerifyReadableLocation
from libs.scholarly_catalog.application.queries.read_evidence_snapshot import ReadEvidenceSnapshot
from libs.scholarly_catalog.domain.services.evidence_snapshot_rules import EvidenceSnapshotRules
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import NormalizePaperIdentifier
from libs.scholarly_catalog.dtos.access_location_claim import AccessLocationClaim
from libs.scholarly_catalog.dtos.access_location_probe import AccessLocationProbe
from libs.scholarly_catalog.dtos.evidence_anchor_request import EvidenceAnchorRequest
from libs.scholarly_catalog.dtos.evidence_preparation_input import EvidencePreparationInput
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.exceptions.access_assessment_error import AccessAssessmentError
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import EvidenceSnapshotError

AT = datetime(2026, 9, 23, tzinfo=timezone.utc)
URL = "https://arxiv.org/abs/2609.00071"
SOURCE_ROOT = Path(__file__).resolve().parents[4]


def compose(root: Path):
    schema = SqliteSchemaConnectionFactory(root, load_workspace_migrations(with_discovery=True))
    factory = SqliteConnectionFactory(root)
    uow = SqliteObjectUnitOfWorkAdapter(factory)
    files = FilesystemObjectBytesAdapter(root)
    objects = KernelEvidenceObjectAdapter(PublishObject(files, uow), ReadObject(files, uow))
    snapshots = SqliteEvidenceSnapshotStoreAdapter(schema.connect)
    rules = EvidenceSnapshotRules()
    access = SqliteAccessAssessmentStoreAdapter(schema.connect)
    resolve = ResolvePaperIdentity(NormalizePaperIdentifier(), SqlitePaperIdentityStoreAdapter(schema.connect))
    verify_access = VerifyReadableLocation(SafeAccessEvidencePolicyAdapter(frozenset({"arxiv"})), access)
    return (
        resolve,
        verify_access,
        access,
        PrepareEvidenceSnapshot(rules, objects, snapshots),
        ReadEvidenceSnapshot(rules, objects, snapshots),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "研究資料"
    SqliteWorkspaceBootstrapAdapter(root, load_workspace_migrations(with_discovery=True)).initialize()
    return root


def sample(resolve, version: int = 1, text: str = "虛構摘要：🏸誤差為0.42公尺。"):
    moment = AT + timedelta(hours=version - 1)
    body = text.encode("utf-8")
    identity = resolve(
        PaperIdentityObservation(
            f"obs:flow:{version}",
            "arxiv",
            f"2609.00071v{version}",
            "Synthetic badminton fixture, not a real paper",
            hashlib.sha256(body).hexdigest(),
            "preprint",
            URL,
            "preprint",
            moment,
            moment,
            AT,
        )
    )
    request = EvidencePreparationInput(
        identity.revision_id,
        identity.work_id,
        body,
        "text/plain; charset=utf-8",
        "synthetic-text-v1",
        text,
        "abstract",
        False,
        ("abstract",),
        (),
        None,
        (EvidenceAnchorRequest(text, 0, len(text), "abstract", "p1"),),
        moment,
    )
    return identity, request


def access_sample(identity, version: int = 1):
    moment = AT + timedelta(hours=version - 1)
    claim = AccessLocationClaim(
        identity.manifestation_id,
        "arxiv",
        URL,
        "abstract",
        "free",
        "permitted",
        ("local_storage",),
        None,
        '{"fixture":"synthetic-policy-evidence-not-a-live-permission"}',
        moment,
        moment + timedelta(hours=1),
        f"arxiv:2609.00071v{version}",
    )
    probe = AccessLocationProbe(
        URL, URL, (), ((URL, ("151.101.1.69",)),), 200, "text/html", "arxiv", f"2609.00071v{version}"
    )
    return claim, probe


def test_versions_access_and_unicode_evidence_reopen_in_another_process(workspace: Path) -> None:
    resolve, verify_access, _, prepare, read = compose(workspace)
    v1, first = sample(resolve)
    v2, second = sample(resolve, 2, "虛構摘要：🏸誤差為0.43公尺。")
    assert v1.work_id == v2.work_id and v1.revision_id != v2.revision_id
    for identity, version, request in ((v1, 1, first), (v2, 2, second)):
        claim, probe = access_sample(identity, version)
        assessed = verify_access(claim, probe)
        assert assessed.content_scope == "abstract" and assessed.reader_access == "free"
        assert "external_model_processing" not in assessed.permitted_uses
        snapshot = prepare(request)
        assert snapshot.evidence_level == "abstract_only"
        assert read(snapshot.snapshot_id).normalized_text == request.normalized_text
    code = (
        "import json,sys; from pathlib import Path; "
        "from libs.scholarly_catalog.tests.integration.test_catalog_evidence_flow import compose; "
        "result=compose(Path(sys.argv[1]))[-1](sys.argv[2]); "
        "print(json.dumps({'text':result.normalized_text,'revision':result.snapshot.revision_id}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(workspace), snapshot.snapshot_id],
        cwd=SOURCE_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"text": second.normalized_text, "revision": v2.revision_id}


def test_corrupt_text_object_is_rejected_by_public_readback(workspace: Path) -> None:
    resolve, _, _, prepare, read = compose(workspace)
    _, request = sample(resolve)
    snapshot = prepare(request)
    digest = snapshot.text_object_id.split(":", 1)[1]
    path = workspace / "objects" / "extracted" / digest[:2] / digest
    original = path.read_bytes()
    path.write_bytes(b"x" * len(original))
    with pytest.raises(StorageError, match="corrupt"):
        read(snapshot.snapshot_id)
    path.write_bytes(original)
    assert read(snapshot.snapshot_id).normalized_text == request.normalized_text


def test_missing_source_is_not_reported_as_empty_evidence(workspace: Path) -> None:
    resolve, _, _, prepare, read = compose(workspace)
    _, request = sample(resolve)
    snapshot = prepare(request)
    kind, digest = snapshot.object_id.split(":", 1)
    path = workspace / "objects" / kind / digest[:2] / digest
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(StorageError, match="missing"):
        read(snapshot.snapshot_id)
    path.write_bytes(original)
    assert read(snapshot.snapshot_id).source_bytes == original


def test_catalog_rollback_leaves_complete_objects_for_identical_retry(workspace: Path) -> None:
    resolve, _, _, prepare, read = compose(workspace)
    _, request = sample(resolve)
    database = workspace / "state/app.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER fail_flow_anchor BEFORE INSERT ON evidence_anchors "
            "BEGIN SELECT RAISE(ABORT,'synthetic injected failure'); END"
        )
    with pytest.raises(EvidenceSnapshotError, match="evidence_database_conflict"):
        prepare(request)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM evidence_anchors").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM object_registry").fetchone()[0] == 2
        connection.execute("DROP TRIGGER fail_flow_anchor")
    snapshot = prepare(request)
    assert read(snapshot.snapshot_id).source_bytes == request.source_bytes
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM object_registry").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 1


def test_expired_new_policy_does_not_change_or_reauthorize_historical_evidence(workspace: Path) -> None:
    resolve, verify_access, access, prepare, read = compose(workspace)
    identity, request = sample(resolve)
    snapshot = prepare(request)
    claim, probe = access_sample(identity)
    verify_access(replace(claim, expires_at=None), probe)
    verify_access(
        replace(
            claim,
            reader_access_signal="restricted",
            automated_retrieval_signal="prohibited",
            permitted_uses=(),
            checked_at=AT + timedelta(minutes=5),
            expires_at=AT + timedelta(minutes=10),
        ),
        probe,
    )
    with pytest.raises(AccessAssessmentError, match="access_assessment_expired"):
        access.read_current(identity.manifestation_id, AT + timedelta(minutes=11))
    assert read(snapshot.snapshot_id).normalized_text == request.normalized_text
