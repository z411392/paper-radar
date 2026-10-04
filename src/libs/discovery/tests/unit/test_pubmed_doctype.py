import pytest

from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.exceptions.source_parse_error import SourceParseError


def _source() -> PubmedSourceAdapter:
    return PubmedSourceAdapter(
        tool="paper-radar",
        email="reader@example.com",
    )


def _xml(doctype: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + doctype
        + '\n<PubmedArticleSet>'
        '<PubmedArticle>'
        '<MedlineCitation>'
        '<PMID>12345678</PMID>'
        '<Article>'
        '<ArticleTitle>Example PubMed paper</ArticleTitle>'
        '<Abstract><AbstractText>Example abstract.</AbstractText></Abstract>'
        '<Journal><Title>Example Journal</Title></Journal>'
        '<Language>eng</Language>'
        '<PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>'
        '</Article>'
        '</MedlineCitation>'
        '<PubmedData><ArticleIdList><ArticleId IdType="pubmed">12345678</ArticleId></ArticleIdList></PubmedData>'
        '</PubmedArticle>'
        '</PubmedArticleSet>'
    ).encode("utf-8")


def test_pubmed_parser_accepts_official_nlm_doctype() -> None:
    source = _source()
    request = source.bibliography_request(("12345678",))
    body = _xml(
        '<!DOCTYPE PubmedArticleSet PUBLIC '
        '"-//NLM//DTD PubMedArticle, 1st January 2026//EN" '
        '"https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_260101.dtd">'
    )

    batch = source.parse_bibliography(request, body, http_status=200)

    assert [record.pmid for record in batch.records] == ["12345678"]


def test_pubmed_parser_rejects_untrusted_external_doctype() -> None:
    source = _source()
    request = source.bibliography_request(("12345678",))
    body = _xml(
        '<!DOCTYPE PubmedArticleSet PUBLIC '
        '"-//NLM//DTD PubMedArticle, 1st January 2026//EN" '
        '"https://example.com/evil.dtd">'
    )

    with pytest.raises(SourceParseError) as exc:
        source.parse_bibliography(request, body, http_status=200)

    assert exc.value.code == "xml_doctype_forbidden"
