import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from libs.scholarly_catalog.application.commands.prepare_evidence_snapshot import PrepareEvidenceSnapshot
from libs.scholarly_catalog.application.queries.read_evidence_snapshot import ReadEvidenceSnapshot
from libs.scholarly_catalog.domain.services.evidence_snapshot_rules import EvidenceSnapshotRules
from libs.scholarly_catalog.dtos.evidence_anchor_request import EvidenceAnchorRequest
from libs.scholarly_catalog.dtos.evidence_object_receipt import EvidenceObjectReceipt
from libs.scholarly_catalog.dtos.evidence_preparation_input import EvidencePreparationInput
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import EvidenceSnapshotError


def request():
    text = "摘要\n🏸誤差為0.42公尺；共180段。"
    quote = "0.42公尺"
    start = text.index(quote)
    return EvidencePreparationInput(
        "revision:fixture",
        "work:fixture",
        b"synthetic abstract",
        "text/plain",
        "fixture-v1",
        text,
        "abstract",
        False,
        ("abstract",),
        (),
        None,
        (EvidenceAnchorRequest(quote, start, start + len(quote), "abstract", "p1"),),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
    )


def prepared(value=None):
    value = value or request()
    source_hash = hashlib.sha256(value.source_bytes).hexdigest()
    text_hash = hashlib.sha256(value.normalized_text.encode()).hexdigest()
    kind = "evidence" if value.content_scope == "abstract" else "fulltext"
    snapshot = EvidenceSnapshotRules.prepare(
        value,
        EvidenceObjectReceipt(f"{kind}:{source_hash}", source_hash),
        EvidenceObjectReceipt(f"extracted:{text_hash}", text_hash),
    )
    return value, snapshot


def rehash_coverage(snapshot, coverage):
    encoded = json.dumps(coverage, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    payload = {
        "format_version": 1,
        "revision_id": snapshot.revision_id,
        "work_id": snapshot.work_id,
        "object_id": snapshot.object_id,
        "text_object_id": snapshot.text_object_id,
        "parser_version": snapshot.parser_version,
        "evidence_level": snapshot.evidence_level,
        "coverage": coverage,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    # Empty anchors isolate coverage validation from cross-snapshot ID rejection.
    return replace(
        snapshot,
        snapshot_id=f"snapshot:{digest}",
        fingerprint=digest,
        coverage_json=encoded,
        anchors=(),
    )


def test_unicode_offsets_are_codepoints_not_bytes():
    value, snapshot = prepared()
    EvidenceSnapshotRules.verify(snapshot, value.source_bytes, value.normalized_text)
    anchor = snapshot.anchors[0]
    assert value.normalized_text[anchor.offset_start : anchor.offset_end] == "0.42公尺"
    assert anchor.offset_start != value.normalized_text.encode().index(b"0.42")


@pytest.mark.parametrize("scope", [[], {}, None, 1, True], ids=["list", "map", "none", "int", "bool"])
def test_invalid_scope_is_a_domain_error(scope):
    with pytest.raises(EvidenceSnapshotError, match="invalid_evidence_coverage"):
        EvidenceSnapshotRules.preview_level(replace(request(), content_scope=scope))


def test_abstract_cannot_claim_body_sections():
    with pytest.raises(EvidenceSnapshotError, match="invalid_evidence_coverage"):
        EvidenceSnapshotRules.preview_level(replace(request(), included_sections=("abstract", "results")))


def test_blank_text_cannot_be_evidence():
    value = replace(request(), normalized_text=" \n", anchors=(EvidenceAnchorRequest(" ", 0, 1),))
    with pytest.raises(EvidenceSnapshotError, match="invalid_evidence_text"):
        EvidenceSnapshotRules.preview_level(value)


@pytest.mark.parametrize("anchors", [(), [], None], ids=["empty-tuple", "list", "none"])
def test_readback_requires_a_nonempty_anchor_tuple(anchors):
    value, snapshot = prepared()
    with pytest.raises(EvidenceSnapshotError, match="invalid_evidence_anchor"):
        EvidenceSnapshotRules.verify(
            replace(snapshot, anchors=anchors), value.source_bytes, value.normalized_text
        )


def test_readback_rejects_abstract_coverage_without_abstract_section():
    value, snapshot = prepared()
    coverage = {
        "content_scope": "abstract",
        "document_complete": False,
        "included_sections": ["methods"],
        "missing_required_sections": [],
    }
    with pytest.raises(EvidenceSnapshotError, match="invalid_evidence_coverage"):
        EvidenceSnapshotRules.verify(
            rehash_coverage(snapshot, coverage), value.source_bytes, value.normalized_text
        )


@pytest.mark.parametrize("content", [None, "not bytes", b""], ids=["none", "text", "empty"])
def test_bad_source_readback_is_a_domain_error(content):
    value, snapshot = prepared()
    with pytest.raises(EvidenceSnapshotError):
        EvidenceSnapshotRules.verify(snapshot, content, value.normalized_text)


def test_surrogate_in_table_locator_is_not_an_uncaught_unicode_error():
    value = request()
    anchor = replace(value.anchors[0], table_locator_json='{"row":"\ud800"}')
    with pytest.raises(EvidenceSnapshotError):
        prepared(replace(value, anchors=(anchor,)))


@pytest.mark.parametrize("field", ["source", "text"])
def test_changed_bytes_are_never_silently_accepted(field):
    value, snapshot = prepared()
    source = value.source_bytes + b"!" if field == "source" else value.source_bytes
    text = value.normalized_text + "!" if field == "text" else value.normalized_text
    with pytest.raises(EvidenceSnapshotError, match="evidence_object_hash_mismatch"):
        EvidenceSnapshotRules.verify(snapshot, source, text)


def test_retry_time_does_not_change_snapshot_or_anchor_identity():
    value, first = prepared()
    _, second = prepared(replace(value, created_at=datetime(2026, 9, 24, tzinfo=timezone.utc)))
    assert first.snapshot_id == second.snapshot_id
    assert first.anchors == second.anchors
    assert first.created_at != second.created_at


def test_changed_parser_creates_a_new_snapshot_and_new_anchors():
    value, first = prepared()
    _, second = prepared(replace(value, parser_version="fixture-v2"))
    assert first.snapshot_id != second.snapshot_id
    assert first.anchors[0].anchor_id != second.anchors[0].anchor_id


def test_boolean_offset_is_not_integer_offset():
    value = request()
    invalid = replace(value, anchors=(replace(value.anchors[0], offset_start=True),))
    with pytest.raises(EvidenceSnapshotError, match="invalid_evidence_anchor"):
        EvidenceSnapshotRules.preview_level(invalid)


@pytest.mark.parametrize("failure", ["parser", "quote"])
def test_public_preparation_rejects_invalid_evidence_before_object_writes(failure):
    value = request()
    if failure == "parser":
        value = replace(value, normalized_text=None, parser_error_code="fixture-failure")
    else:
        value = replace(value, anchors=(replace(value.anchors[0], quote="invented"),))
    objects, snapshots = Mock(), Mock()
    prepare = PrepareEvidenceSnapshot(EvidenceSnapshotRules(), objects, snapshots)
    with pytest.raises(EvidenceSnapshotError):
        prepare(value)
    objects.publish_source.assert_not_called()
    objects.publish_text.assert_not_called()
    snapshots.save.assert_not_called()


def test_public_readback_verifies_actual_text_bytes():
    value, snapshot = prepared()
    objects = Mock()
    objects.read.side_effect = [value.source_bytes, value.normalized_text.replace("0.42", "0.58").encode()]
    snapshots = Mock()
    snapshots.read.return_value = snapshot
    with pytest.raises(EvidenceSnapshotError, match="evidence_object_hash_mismatch"):
        ReadEvidenceSnapshot(EvidenceSnapshotRules(), objects, snapshots)(snapshot.snapshot_id)


def test_public_readback_rejects_non_utf8_without_fallback():
    value, snapshot = prepared()
    objects = Mock()
    objects.read.side_effect = [value.source_bytes, b"\xff"]
    snapshots = Mock()
    snapshots.read.return_value = snapshot
    with pytest.raises(EvidenceSnapshotError, match="evidence_text_not_utf8"):
        ReadEvidenceSnapshot(EvidenceSnapshotRules(), objects, snapshots)(snapshot.snapshot_id)


def test_public_readback_returns_original_bytes_and_exact_unicode_text():
    value, snapshot = prepared()
    objects = Mock()
    objects.read.side_effect = [value.source_bytes, value.normalized_text.encode()]
    snapshots = Mock()
    snapshots.read.return_value = snapshot
    actual = ReadEvidenceSnapshot(EvidenceSnapshotRules(), objects, snapshots)(snapshot.snapshot_id)
    assert actual.snapshot == snapshot
    assert actual.source_bytes == value.source_bytes
    assert actual.normalized_text == value.normalized_text
