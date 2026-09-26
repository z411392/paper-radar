import pytest

from libs.scholarly_catalog.application.commands.bind_manifestation_identifier import (
    BindManifestationIdentifier,
)
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.dtos.normalized_identifier import NormalizedIdentifier
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class RecordingStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, NormalizedIdentifier, str]] = []

    def bind_identifier(
        self,
        manifestation_id: str,
        identifier: NormalizedIdentifier,
        source_evidence_id: str,
    ) -> None:
        self.calls.append(
            (manifestation_id, identifier, source_evidence_id)
        )


def test_bind_manifestation_identifier_normalizes_before_store() -> None:
    store = RecordingStore()
    command = BindManifestationIdentifier(
        NormalizePaperIdentifier(),
        store,
    )

    result = command(
        manifestation_id="manifestation:" + "a" * 64,
        namespace="doi",
        identifier_value="https://doi.org/10.1234/Example",
        source_evidence_id="evidence:crosswalk:1",
    )

    assert result is None
    assert store.calls == [
        (
            "manifestation:" + "a" * 64,
            NormalizedIdentifier("doi", "10.1234/example", None),
            "evidence:crosswalk:1",
        )
    ]


def test_invalid_identifier_fails_before_store_binding() -> None:
    store = RecordingStore()
    command = BindManifestationIdentifier(
        NormalizePaperIdentifier(),
        store,
    )

    with pytest.raises(PaperIdentityError, match="invalid_identifier"):
        command(
            manifestation_id="manifestation:" + "a" * 64,
            namespace="doi",
            identifier_value="not-a-doi",
            source_evidence_id="evidence:crosswalk:1",
        )

    assert store.calls == []
