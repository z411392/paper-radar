import pytest

from libs.scholarly_catalog.domain.services.classify_revision_change import (
    ClassifyRevisionChange,
    RevisionChangeError,
)


A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64


def classify(**changes):
    values = {
        "previous_content_fingerprint": A,
        "current_content_fingerprint": A,
        "previous_metadata_fingerprint": C,
        "current_metadata_fingerprint": C,
        "previous_publication_status": "published",
        "current_publication_status": "published",
        "integrity_event_kind": None,
    }
    values.update(changes)
    return ClassifyRevisionChange()(**values)


def test_content_change_is_the_only_regeneration_trigger() -> None:
    result = classify(current_content_fingerprint=B)

    assert result.event_kinds == ("revision_available",)
    assert result.content_changed is True
    assert result.metadata_changed is False
    assert result.publication_status_changed is False
    assert result.requires_regeneration is True


def test_semantic_metadata_only_change_does_not_regenerate_content() -> None:
    result = classify(current_metadata_fingerprint=D)

    assert result.event_kinds == ("metadata_changed",)
    assert result.content_changed is False
    assert result.metadata_changed is True
    assert result.requires_regeneration is False


def test_publication_status_only_change_does_not_regenerate_content() -> None:
    result = classify(current_publication_status="corrected")

    assert result.event_kinds == ("publication_status_changed",)
    assert result.publication_status_changed is True
    assert result.requires_regeneration is False


@pytest.mark.parametrize("kind", ["correction", "retraction"])
def test_integrity_event_alone_does_not_regenerate_content(kind: str) -> None:
    result = classify(integrity_event_kind=kind)

    assert result.event_kinds == (kind,)
    assert result.content_changed is False
    assert result.metadata_changed is False
    assert result.requires_regeneration is False


def test_provider_only_volatility_or_unrelated_configuration_is_no_change() -> None:
    result = classify()

    assert result.event_kinds == ()
    assert result.content_changed is False
    assert result.metadata_changed is False
    assert result.publication_status_changed is False
    assert result.requires_regeneration is False


def test_content_change_subsumes_metadata_event_but_keeps_status_event() -> None:
    result = classify(
        current_content_fingerprint=B,
        current_metadata_fingerprint=D,
        current_publication_status="corrected",
    )

    assert result.event_kinds == (
        "revision_available",
        "publication_status_changed",
    )
    assert result.content_changed is True
    assert result.metadata_changed is True
    assert result.publication_status_changed is True
    assert result.requires_regeneration is True


def test_first_observation_is_not_misclassified_as_a_revision() -> None:
    result = classify(
        previous_content_fingerprint=None,
        previous_metadata_fingerprint=None,
        previous_publication_status=None,
    )

    assert result.event_kinds == ()
    assert result.requires_regeneration is False


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("current_content_fingerprint", "not-a-hash", "invalid_revision_fingerprint"),
        ("current_metadata_fingerprint", "f" * 63, "invalid_revision_fingerprint"),
        ("current_publication_status", "invalid", "invalid_publication_status"),
        ("integrity_event_kind", "expression_of_concern", "unsupported_integrity_event_kind"),
    ],
)
def test_invalid_revision_inputs_fail_closed(
    field: str,
    value: object,
    code: str,
) -> None:
    with pytest.raises(RevisionChangeError, match=code):
        classify(**{field: value})
