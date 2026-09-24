from urllib.parse import urlsplit

from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


_ALLOWED = {
    "pubmed": (
        "eutils.ncbi.nlm.nih.gov",
        frozenset(
            {
                "/entrez/eutils/esearch.fcgi",
                "/entrez/eutils/efetch.fcgi",
            }
        ),
    ),
    "pmc-oai": (
        "pmc.ncbi.nlm.nih.gov",
        frozenset({"/api/oai/v1/mh/"}),
    ),
}


def validate_ncbi_page_request(request: SourcePageRequest) -> tuple[str, str]:
    if (
        not isinstance(request, SourcePageRequest)
        or request.source_id not in _ALLOWED
        or request.method != "GET"
        or not isinstance(request.url, str)
        or len(request.url) > 32768
        or any(ord(char) < 32 or ord(char) == 127 for char in request.url)
    ):
        raise SourceFetchError("invalid_source_request")
    try:
        parsed = urlsplit(request.url)
        port = parsed.port
    except ValueError:
        raise SourceFetchError("invalid_source_request") from None
    expected_host, paths = _ALLOWED[request.source_id]
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in paths
        or parsed.fragment
    ):
        raise SourceFetchError("invalid_source_request")
    target = parsed.path + ("?" + parsed.query if parsed.query else "")
    return expected_host, target
