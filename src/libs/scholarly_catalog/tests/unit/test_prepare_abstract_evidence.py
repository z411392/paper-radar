from datetime import datetime, timezone

import pytest

from libs.scholarly_catalog.application.commands.prepare_abstract_evidence import (
    PrepareAbstractEvidence,
)
from libs.scholarly_catalog.dtos.abstract_evidence import AbstractEvidenceRequest
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import (
    EvidenceSnapshotError,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


class Prepare:
    def __init__(self) -> None:
        self.values = []

    def __call__(self, value):
        self.values.append(value)
        return EvidenceSnapshot(
            "snapshot:" + "a" * 64,
            value.revision_id,
            value.work_id,
            "evidence:" + "b" * 64,
            "extracted:" + "c" * 64,
            value.parser_version,
            "abstract_only",
            '{"content_scope":"abstract","document_complete":false,'
            '"included_sections":["abstract"],"missing_required_sections":[]}',
            "d" * 64,
            NOW.isoformat(),
            (),
        )


def test_abstract_evidence_builds_full_abstract_anchor() -> None:
    prepare = Prepare()
    command = PrepareAbstractEvidence(prepare)

    result = command(
        AbstractEvidenceRequest(
            "revision:fixture",
            "work:fixture",
            "arxiv-atom-v1",
            "  line one\r\nline two  ",
            NOW,
        )
    )

    assert result.state == "available"
    assert result.snapshot_id == "snapshot:" + "a" * 64
    value = prepare.values[0]
    assert value.normalized_text == "line one\nline two"
    assert value.source_bytes == b"line one\nline two"
    assert value.parser_version == "arxiv-atom-v1:abstract-v1"
    assert value.content_scope == "abstract"
    assert value.document_complete is False
    assert value.included_sections == ("abstract",)
    assert value.missing_required_sections == ()
    assert len(value.anchors) == 1
    anchor = value.anchors[0]
    assert anchor.quote == value.normalized_text
    assert (anchor.offset_start, anchor.offset_end) == (
        0,
        len(value.normalized_text),
    )
    assert anchor.section_label == anchor.paragraph_id == "abstract"


def test_missing_abstract_is_explicit_unavailable_without_side_effect() -> None:
    prepare = Prepare()
    result = PrepareAbstractEvidence(prepare)(
        AbstractEvidenceRequest(
            "revision:fixture",
            "work:fixture",
            "pubmed-eutils-parser-v1",
            None,
            NOW,
        )
    )

    assert result.state == "unavailable"
    assert result.snapshot_id is None
    assert prepare.values == []


@pytest.mark.parametrize("abstract", ["", "   ", "bad\x00text"])
def test_invalid_present_abstract_is_not_silently_treated_as_missing(
    abstract: str,
) -> None:
    prepare = Prepare()
    with pytest.raises(EvidenceSnapshotError, match="invalid_abstract_evidence"):
        PrepareAbstractEvidence(prepare)(
            AbstractEvidenceRequest(
                "revision:fixture",
                "work:fixture",
                "arxiv-atom-v1",
                abstract,
                NOW,
            )
        )
    assert prepare.values == []
