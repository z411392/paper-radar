from dataclasses import replace

import pytest

from libs.discovery.exceptions.source_parse_error import SourceParseError
from libs.scholarly_catalog.adapters.driven.pmc_full_text_adapter import (
    PmcFullTextAdapter,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/?verb=GetRecord",
        "https://127.0.0.1/api/oai/v1/mh/?verb=GetRecord",
        "https://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "https://attacker.example/api/oai/v1/mh/?verb=GetRecord",
        (
            "https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/"
            "?verb=GetRecord&identifier=oai%3Apubmedcentral.nih.gov%3A123"
            "&metadataPrefix=pmc&url=https%3A%2F%2F127.0.0.1"
        ),
    ],
)
def test_forged_pmc_request_cannot_select_another_network_target(url: str) -> None:
    adapter = PmcFullTextAdapter()
    request = replace(adapter.request("PMC123"), url=url)

    with pytest.raises(SourceParseError, match="invalid_page_request"):
        adapter.parse(request, b"<not-used/>", http_status=200)


def test_pmc_parser_rejects_doctype_before_any_entity_can_be_used() -> None:
    adapter = PmcFullTextAdapter()
    request = adapter.request("PMC123")
    body = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
        b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">&secret;</OAI-PMH>'
    )

    with pytest.raises(SourceParseError, match="xml_doctype_forbidden"):
        adapter.parse(request, body, http_status=200)


def test_pmc_parser_rejects_excessive_nesting() -> None:
    adapter = PmcFullTextAdapter()
    request = adapter.request("PMC123")
    body = (
        b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
        + b"<x>" * 65
        + b"</x>" * 65
        + b"</OAI-PMH>"
    )

    with pytest.raises(SourceParseError, match="xml_depth_limit"):
        adapter.parse(request, body, http_status=200)


def test_pmc_parser_rejects_oversized_body_before_xml_processing() -> None:
    adapter = PmcFullTextAdapter()
    request = adapter.request("PMC123")

    with pytest.raises(SourceParseError, match="response_too_large"):
        adapter.parse(request, b"x" * 8_000_001, http_status=200)
