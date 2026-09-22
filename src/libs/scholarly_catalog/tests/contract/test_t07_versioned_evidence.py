import pytest

from libs.scholarly_catalog.domain.services.normalize_paper_identifier import NormalizePaperIdentifier
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
