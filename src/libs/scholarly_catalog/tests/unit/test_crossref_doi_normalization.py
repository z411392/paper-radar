import pytest

from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


def test_doi_uri_percent_decodes_exactly_once_and_ascii_folds() -> None:
    normalizer = NormalizePaperIdentifier()

    decoded = normalizer("doi", "https://doi.org/10.1000/ABC%2FDef")
    once = normalizer("doi", "https://doi.org/10.1000/ABC%252FDef")

    assert decoded.normalized_value == "10.1000/abc/def"
    assert once.normalized_value == "10.1000/abc%2fdef"


def test_bare_doi_keeps_literal_percent_and_only_ascii_case_folds() -> None:
    value = NormalizePaperIdentifier()("doi", "10.1000/ÄBC%2FDEF")

    assert value.normalized_value == "10.1000/Äbc%2fdef"


@pytest.mark.parametrize(
    "value",
    [
        "https://doi.org/10.1000/abc%ZZ",
        "https://doi.org/10.1000/abc?query=1",
        "https://doi.org/10.1000/abc#fragment",
    ],
)
def test_wrapped_doi_rejects_malformed_escape_query_or_fragment(value: str) -> None:
    with pytest.raises(PaperIdentityError, match="invalid_identifier"):
        NormalizePaperIdentifier()("doi", value)
