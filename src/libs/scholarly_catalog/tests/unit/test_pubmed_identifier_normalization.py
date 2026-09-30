import pytest

from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


def test_pmid_is_canonical_numeric_identity_without_version() -> None:
    value = NormalizePaperIdentifier()("pmid", "00012345")
    assert (value.namespace, value.normalized_value, value.native_version) == (
        "pmid",
        "12345",
        None,
    )


def test_pmc_is_canonical_uppercase_identity_without_version() -> None:
    value = NormalizePaperIdentifier()("pmc", "pmc0012345")
    assert (value.namespace, value.normalized_value, value.native_version) == (
        "pmc",
        "PMC12345",
        None,
    )


@pytest.mark.parametrize(
    "namespace,value",
    [
        ("pmid", "0"),
        ("pmid", "-1"),
        ("pmid", "123 456"),
        ("pmid", "https://pubmed.ncbi.nlm.nih.gov/123/"),
        ("pmc", "PMC0"),
        ("pmc", "12345"),
        ("pmc", "PMC12A"),
        ("pmc", "https://pmc.ncbi.nlm.nih.gov/articles/PMC12345/"),
    ],
)
def test_pubmed_identifiers_reject_non_identifier_forms(namespace: str, value: str) -> None:
    with pytest.raises(PaperIdentityError, match="invalid_identifier"):
        NormalizePaperIdentifier()(namespace, value)
