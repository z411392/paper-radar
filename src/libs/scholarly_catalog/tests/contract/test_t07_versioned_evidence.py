import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.scholarly_catalog.adapters.driven.sqlite_paper_identity_store_adapter import (
    SqlitePaperIdentityStoreAdapter,
)
from libs.scholarly_catalog.application.commands.merge_paper_work_alias import MergePaperWorkAlias
from libs.scholarly_catalog.application.commands.record_paper_work_relation import RecordPaperWorkRelation
from libs.scholarly_catalog.application.commands.resolve_paper_identity import ResolvePaperIdentity
from libs.scholarly_catalog.application.commands.revoke_paper_work_alias import RevokePaperWorkAlias
from libs.scholarly_catalog.application.queries.read_paper_identity import ReadPaperIdentity
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import NormalizePaperIdentifier
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


def test_doi_display_forms_and_case_normalize_to_one_identity() -> None:
    normalize = NormalizePaperIdentifier()
    values = (
        "10.1234/ABC.Def",
        "doi:10.1234/abc.def",
        "https://doi.org/10.1234/ABC.Def",
        "http://dx.doi.org/10.1234/abc.def",
    )
    results = tuple(normalize("doi", value) for value in values)
    assert {item.normalized_value for item in results} == {"10.1234/abc.def"}
    assert {item.native_version for item in results} == {None}


def test_arxiv_versions_share_base_identity_and_keep_exact_version() -> None:
    normalize = NormalizePaperIdentifier()
    v1 = normalize("arxiv", "arXiv:2609.00001v1")
    v2 = normalize("arxiv", "https://arxiv.org/abs/2609.00001v2")
    latest = normalize("arxiv", "2609.00001")
    assert v1.normalized_value == v2.normalized_value == latest.normalized_value == "2609.00001"
    assert (v1.native_version, v2.native_version, latest.native_version) == ("1", "2", None)


def test_legacy_arxiv_id_keeps_base_and_version() -> None:
    value = NormalizePaperIdentifier()("arxiv", "hep-th/9901001v3")
    assert value.normalized_value == "hep-th/9901001"
    assert value.native_version == "3"


@pytest.mark.parametrize(
    ("namespace", "value"),
    [
        ("arxiv", "2613.00001v1"),
        ("arxiv", "2609.00001v0"),
        ("arxiv", "https://example.com/abs/2609.00001v1"),
        ("doi", "https://doi.org/10.1234/abc?query=1"),
        ("doi", "10.1234/no space allowed"),
        ("doi", "11.1234/not-a-doi"),
        ("pmid", "12345"),
    ],
)
def test_invalid_or_unsupported_identifier_is_rejected(namespace: str, value: str) -> None:
    with pytest.raises(PaperIdentityError):
        NormalizePaperIdentifier()(namespace, value)


AT = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)


def _identity_tools(tmp_path: Path):
    root = tmp_path / "workspace"
    migrations = load_workspace_migrations(with_discovery=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    connection = SqliteSchemaConnectionFactory(root, migrations, minimum_version=3)
    store = SqlitePaperIdentityStoreAdapter(connection.connect)
    normalize = NormalizePaperIdentifier()
    return root, ResolvePaperIdentity(normalize, store), ReadPaperIdentity(normalize, store)


def _observation(
    source_observation_id: str,
    identifier: str,
    fingerprint: str,
    *,
    namespace: str = "arxiv",
    title: str = "A conservative identity fixture",
    observed_at: datetime = AT,
    source_updated_at: datetime | None = AT,
    published_at: datetime | None = AT,
    manifestation_kind: str = "preprint",
    publication_status: str = "preprint",
    landing_url: str | None = None,
) -> PaperIdentityObservation:
    normalized = NormalizePaperIdentifier()(namespace, identifier)
    canonical_url = (
        "https://doi.org/" + normalized.normalized_value
        if namespace == "doi"
        else "https://arxiv.org/abs/" + normalized.normalized_value
    )
    return PaperIdentityObservation(
        source_observation_id=source_observation_id,
        identifier_namespace=namespace,
        identifier_value=identifier,
        title=title,
        content_fingerprint=fingerprint,
        manifestation_kind=manifestation_kind,
        landing_url=landing_url or canonical_url,
        publication_status=publication_status,
        observed_at=observed_at,
        source_updated_at=source_updated_at,
        published_at=published_at,
    )


def test_same_arxiv_base_records_distinct_revisions_under_one_work(tmp_path: Path) -> None:
    _, resolve, read = _identity_tools(tmp_path)
    first = resolve(_observation("obs:arxiv:v1", "arXiv:2609.00001v1", "1" * 64))
    second = resolve(
        _observation(
            "obs:arxiv:v2",
            "https://arxiv.org/abs/2609.00001v2",
            "2" * 64,
            observed_at=AT + timedelta(hours=1),
            source_updated_at=AT + timedelta(hours=1),
        )
    )
    assert first.work_id == second.work_id
    assert first.manifestation_id == second.manifestation_id
    assert first.revision_id != second.revision_id
    view = read("arxiv", "2609.00001")
    assert view.work_id == view.canonical_work_id == first.work_id
    assert view.manifestation_id == first.manifestation_id
    assert tuple(item.native_version for item in view.revisions) == ("1", "2")
    assert tuple(item.content_fingerprint for item in view.revisions) == ("1" * 64, "2" * 64)


def test_exact_replay_is_idempotent_and_does_not_add_revision(tmp_path: Path) -> None:
    _, resolve, read = _identity_tools(tmp_path)
    observation = _observation("obs:replay", "2609.00002v1", "3" * 64)
    first = resolve(observation)
    second = resolve(observation)
    assert (first.work_id, first.manifestation_id, first.revision_id) == (
        second.work_id,
        second.manifestation_id,
        second.revision_id,
    )
    assert first.created_work and first.created_manifestation and first.created_revision
    assert not second.created_work and not second.created_manifestation and not second.created_revision
    assert len(read("arxiv", "2609.00002").revisions) == 1


def test_same_title_with_different_exact_identifiers_stays_separate(tmp_path: Path) -> None:
    _, resolve, _ = _identity_tools(tmp_path)
    left = resolve(_observation("obs:left", "2609.00003v1", "4" * 64, title="Same title"))
    right = resolve(_observation("obs:right", "2609.00004v1", "5" * 64, title="Same title"))
    assert left.work_id != right.work_id
    assert left.manifestation_id != right.manifestation_id


def test_doi_forms_share_identity_and_keep_each_source_observation_as_provenance(
    tmp_path: Path,
) -> None:
    root, resolve, read = _identity_tools(tmp_path)
    first = resolve(
        _observation(
            "obs:doi:one",
            "10.1234/ABC.Def",
            "6" * 64,
            namespace="doi",
            manifestation_kind="publication",
            publication_status="published",
        )
    )
    second = resolve(
        _observation(
            "obs:doi:two",
            "doi:10.1234/abc.def",
            "6" * 64,
            namespace="doi",
            manifestation_kind="publication",
            publication_status="published",
            observed_at=AT + timedelta(minutes=5),
        )
    )
    assert first.work_id == second.work_id
    assert first.manifestation_id == second.manifestation_id
    assert first.revision_id == second.revision_id
    assert len(read("doi", "https://doi.org/10.1234/ABC.Def").revisions) == 1
    connection = SqliteConnectionFactory(root).connect()
    try:
        rows = connection.execute(
            "SELECT DISTINCT source_observation_id FROM catalog_field_provenance "
            "WHERE work_id=? ORDER BY source_observation_id",
            (first.work_id,),
        ).fetchall()
    finally:
        connection.close()
    assert tuple(row[0] for row in rows) == ("obs:doi:one", "obs:doi:two")


def test_same_source_observation_cannot_be_reinterpreted_with_different_title(
    tmp_path: Path,
) -> None:
    _, resolve, read = _identity_tools(tmp_path)
    resolve(_observation("obs:conflict", "2609.00005v1", "7" * 64, title="Original"))
    with pytest.raises(PaperIdentityError, match="source_observation_conflict"):
        resolve(
            _observation(
                "obs:conflict",
                "2609.00005v2",
                "8" * 64,
                title="Reinterpreted",
                observed_at=AT + timedelta(hours=1),
            )
        )
    view = read("arxiv", "2609.00005")
    assert len(view.revisions) == 1
    assert view.revisions[0].title == "Original"


def test_same_content_fingerprint_cannot_describe_different_revision_content(tmp_path: Path) -> None:
    _, resolve, read = _identity_tools(tmp_path)
    resolve(_observation("obs:fingerprint:one", "2609.00006v1", "9" * 64, title="Original"))
    with pytest.raises(PaperIdentityError, match="revision_conflict"):
        resolve(
            _observation(
                "obs:fingerprint:two",
                "2609.00006v2",
                "9" * 64,
                title="Different",
                observed_at=AT + timedelta(hours=1),
            )
        )
    assert len(read("arxiv", "2609.00006").revisions) == 1



def _three_works(tmp_path: Path):
    root, resolve, read = _identity_tools(tmp_path)
    left = resolve(_observation("obs:alias:left", "2609.00011v1", "a" * 64, title="Shared title"))
    right = resolve(_observation("obs:alias:right", "2609.00012v1", "b" * 64, title="Shared title"))
    third = resolve(_observation("obs:alias:third", "2609.00013v1", "c" * 64, title="Another"))
    migrations = load_workspace_migrations(with_discovery=True)
    connection = SqliteSchemaConnectionFactory(root, migrations, minimum_version=3)
    store = SqlitePaperIdentityStoreAdapter(connection.connect)
    return root, read, store, left, right, third


def test_explicit_alias_merge_requires_evidence_and_resolves_canonical(tmp_path: Path) -> None:
    root, read, store, canonical, alias, _ = _three_works(tmp_path)
    evidence = '{"reason":"same-study","source_observation_ids":["obs:alias:left","obs:alias:right"]}'
    result = MergePaperWorkAlias(store)(
        alias.work_id,
        canonical.work_id,
        evidence,
        decided_at=AT + timedelta(hours=2),
    )
    assert result.alias_work_id == alias.work_id
    assert result.canonical_work_id == canonical.work_id
    assert read("arxiv", "2609.00012").work_id == alias.work_id
    assert read("arxiv", "2609.00012").canonical_work_id == canonical.work_id
    assert read("arxiv", "2609.00011").canonical_work_id == canonical.work_id

    connection = SqliteConnectionFactory(root).connect()
    try:
        row = connection.execute(
            "SELECT decision_evidence_json FROM work_aliases WHERE alias_work_id=?",
            (alias.work_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert json.loads(row[0])["reason"] == "same-study"


def test_alias_without_evidence_is_rejected_and_similar_titles_stay_separate(tmp_path: Path) -> None:
    _, read, store, left, right, _ = _three_works(tmp_path)
    assert left.work_id != right.work_id
    with pytest.raises(PaperIdentityError, match="identity_evidence_required"):
        MergePaperWorkAlias(store)(
            right.work_id,
            left.work_id,
            "{}",
            decided_at=AT + timedelta(hours=2),
        )
    assert read("arxiv", "2609.00011").canonical_work_id == left.work_id
    assert read("arxiv", "2609.00012").canonical_work_id == right.work_id


def test_alias_cycle_and_silent_retarget_are_rejected(tmp_path: Path) -> None:
    _, read, store, left, right, third = _three_works(tmp_path)
    merge = MergePaperWorkAlias(store)
    evidence = '{"reason":"same-study"}'
    merge(right.work_id, left.work_id, evidence, decided_at=AT + timedelta(hours=2))

    with pytest.raises(PaperIdentityError, match="alias_cycle"):
        merge(left.work_id, right.work_id, evidence, decided_at=AT + timedelta(hours=3))
    with pytest.raises(PaperIdentityError, match="alias_conflict"):
        merge(right.work_id, third.work_id, evidence, decided_at=AT + timedelta(hours=3))

    assert read("arxiv", "2609.00012").canonical_work_id == left.work_id


def test_correction_relation_is_recorded_without_merging_works(tmp_path: Path) -> None:
    root, read, store, original, correction, _ = _three_works(tmp_path)
    evidence = '{"reason":"explicit-correction","source_observation_ids":["obs:alias:left"]}'
    result = RecordPaperWorkRelation(store)(
        correction.work_id,
        original.work_id,
        "correction",
        evidence,
        observed_at=AT + timedelta(hours=2),
    )
    assert result.relation_type == "correction"
    assert read("arxiv", "2609.00011").canonical_work_id == original.work_id
    assert read("arxiv", "2609.00012").canonical_work_id == correction.work_id

    connection = SqliteConnectionFactory(root).connect()
    try:
        row = connection.execute(
            "SELECT relation_type,evidence_json FROM work_relations WHERE id=?",
            (result.relation_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None and row[0] == "correction"
    assert json.loads(row[1])["reason"] == "explicit-correction"


def test_alias_revocation_restores_original_identity_and_preserves_merge_evidence(
    tmp_path: Path,
) -> None:
    root, read, store, canonical, alias, _ = _three_works(tmp_path)
    merge_evidence = '{"reason":"same-study","ticket":"decision-1"}'
    MergePaperWorkAlias(store)(
        alias.work_id,
        canonical.work_id,
        merge_evidence,
        decided_at=AT + timedelta(hours=2),
    )
    revocation = RevokePaperWorkAlias(store)(
        alias.work_id,
        '{"reason":"merge-revoked","ticket":"decision-2"}',
        revoked_at=AT + timedelta(hours=3),
    )
    assert revocation.relation_type == "alias_revoked"
    assert read("arxiv", "2609.00012").canonical_work_id == alias.work_id

    connection = SqliteConnectionFactory(root).connect()
    try:
        alias_row = connection.execute(
            "SELECT 1 FROM work_aliases WHERE alias_work_id=?", (alias.work_id,)
        ).fetchone()
        relation = connection.execute(
            "SELECT evidence_json FROM work_relations WHERE id=?",
            (revocation.relation_id,),
        ).fetchone()
    finally:
        connection.close()
    assert alias_row is None
    saved = json.loads(relation[0])
    assert json.loads(saved["merge_evidence_json"])["ticket"] == "decision-1"
    assert json.loads(saved["revocation_evidence_json"])["ticket"] == "decision-2"
