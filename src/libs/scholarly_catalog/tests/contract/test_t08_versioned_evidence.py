import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.scholarly_catalog.adapters.driven.safe_access_evidence_policy_adapter import (
    SafeAccessEvidencePolicyAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_access_assessment_store_adapter import (
    SqliteAccessAssessmentStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_paper_identity_store_adapter import (
    SqlitePaperIdentityStoreAdapter,
)
from libs.scholarly_catalog.application.commands.resolve_paper_identity import ResolvePaperIdentity
from libs.scholarly_catalog.application.commands.verify_readable_location import VerifyReadableLocation
from libs.scholarly_catalog.application.queries.read_current_access_assessment import (
    ReadCurrentAccessAssessment,
)
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import NormalizePaperIdentifier
from libs.scholarly_catalog.dtos.access_location_claim import AccessLocationClaim
from libs.scholarly_catalog.dtos.access_location_probe import AccessLocationProbe
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError

AT = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)


def _tools(tmp_path: Path):
    root = tmp_path / "workspace"
    migrations = load_workspace_migrations(with_discovery=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    connection = SqliteSchemaConnectionFactory(root, migrations, minimum_version=3)
    identity_store = SqlitePaperIdentityStoreAdapter(connection.connect)
    resolved = ResolvePaperIdentity(NormalizePaperIdentifier(), identity_store)(
        PaperIdentityObservation(
            "obs:access:one",
            "arxiv",
            "2609.00021v1",
            "Access fixture",
            "d" * 64,
            "preprint",
            "https://arxiv.org/abs/2609.00021",
            "preprint",
            AT,
            AT,
            AT,
        )
    )
    store = SqliteAccessAssessmentStoreAdapter(connection.connect)
    verify = VerifyReadableLocation(
        SafeAccessEvidencePolicyAdapter(frozenset({"arxiv", "crossref"})),
        store,
    )
    return root, resolved, store, verify


def _claim(
    manifestation_id: str,
    *,
    content_scope: str = "full_text",
    reader: str = "free",
    automated: str = "permitted",
    uses: tuple[str, ...] = ("local_reading", "local_storage", "local_research_processing"),
    source: str = "arxiv",
    checked_at: datetime = AT,
    expires_at: datetime | None = AT + timedelta(days=1),
) -> AccessLocationClaim:
    return AccessLocationClaim(
        manifestation_id,
        source,
        "https://arxiv.org/pdf/2609.00021",
        content_scope,
        reader,
        automated,
        uses,
        None,
        '{"policy":"arxiv-api-terms","source":"official"}',
        checked_at,
        expires_at,
        "arxiv:2609.00021v1",
    )


def _probe(
    *,
    final_url: str = "https://arxiv.org/pdf/2609.00021",
    redirects: tuple[str, ...] = (),
    ips: tuple[str, ...] = ("151.101.1.69",),
    status: int = 200,
    namespace: str | None = "arxiv",
    identifier: str | None = "2609.00021v1",
) -> AccessLocationProbe:
    return AccessLocationProbe(
        "https://arxiv.org/pdf/2609.00021",
        final_url,
        redirects,
        ips,
        status,
        "application/pdf",
        namespace,
        identifier,
    )


def test_free_full_text_does_not_imply_external_model_permission(tmp_path: Path) -> None:
    _, resolved, _, verify = _tools(tmp_path)
    assessment = verify(_claim(resolved.manifestation_id), _probe())
    assert assessment.reader_access == "free"
    assert assessment.automated_retrieval == "permitted"
    assert assessment.permitted_uses == (
        "local_reading",
        "local_storage",
        "local_research_processing",
    )
    assert "external_model_processing" not in assessment.permitted_uses
    evidence = json.loads(assessment.evidence_json)
    assert evidence["content_scope"] == "full_text"
    assert evidence["evidence_source_id"] == "arxiv"


def test_unknown_automated_retrieval_stays_unknown(tmp_path: Path) -> None:
    _, resolved, _, verify = _tools(tmp_path)
    assessment = verify(
        _claim(resolved.manifestation_id, automated="unknown"),
        _probe(),
    )
    assert assessment.reader_access == "free"
    assert assessment.automated_retrieval == "unknown"


def test_abstract_only_cannot_be_recorded_as_free_full_text(tmp_path: Path) -> None:
    _, resolved, _, verify = _tools(tmp_path)
    assessment = verify(
        _claim(resolved.manifestation_id, content_scope="abstract"),
        _probe(),
    )
    assert assessment.content_scope == "abstract"
    assert assessment.reader_access == "unknown"


def test_assessment_persists_source_time_version_and_replays_idempotently(tmp_path: Path) -> None:
    _, resolved, store, verify = _tools(tmp_path)
    first = verify(_claim(resolved.manifestation_id), _probe())
    second = verify(_claim(resolved.manifestation_id), _probe())
    assert first == second
    current = ReadCurrentAccessAssessment(store)(resolved.manifestation_id, AT + timedelta(hours=1))
    assert current == first
    assert current.checked_at == AT.isoformat()
    assert current.content_version_binding == "arxiv:2609.00021v1"


def test_wrong_paper_identity_is_rejected_before_persistence(tmp_path: Path) -> None:
    _, resolved, _, verify = _tools(tmp_path)
    with pytest.raises(PaperIdentityError, match="access_identity_mismatch"):
        verify(
            _claim(resolved.manifestation_id),
            _probe(identifier="2609.99999v1"),
        )


@pytest.mark.parametrize(
    "ips",
    [
        ("127.0.0.1",),
        ("10.0.0.5",),
        ("169.254.169.254",),
        ("::1",),
    ],
)
def test_private_or_local_network_targets_are_rejected(tmp_path: Path, ips: tuple[str, ...]) -> None:
    _, resolved, _, verify = _tools(tmp_path)
    with pytest.raises(PaperIdentityError, match="unsafe_access_target"):
        verify(_claim(resolved.manifestation_id), _probe(ips=ips))


def test_redirect_to_private_network_is_rejected(tmp_path: Path) -> None:
    _, resolved, _, verify = _tools(tmp_path)
    with pytest.raises(PaperIdentityError, match="unsafe_access_target"):
        verify(
            _claim(resolved.manifestation_id),
            _probe(
                final_url="https://127.0.0.1/private.pdf",
                redirects=("https://127.0.0.1/private.pdf",),
                ips=("127.0.0.1",),
            ),
        )


def test_unapproved_evidence_source_is_rejected(tmp_path: Path) -> None:
    _, resolved, _, verify = _tools(tmp_path)
    with pytest.raises(PaperIdentityError, match="unsupported_access_evidence_source"):
        verify(_claim(resolved.manifestation_id, source="random-blog"), _probe())


def test_expired_assessment_is_not_returned_as_current(tmp_path: Path) -> None:
    _, resolved, store, verify = _tools(tmp_path)
    verify(
        _claim(
            resolved.manifestation_id,
            expires_at=AT + timedelta(minutes=5),
        ),
        _probe(),
    )
    with pytest.raises(PaperIdentityError, match="access_assessment_expired"):
        ReadCurrentAccessAssessment(store)(
            resolved.manifestation_id,
            AT + timedelta(minutes=6),
        )
