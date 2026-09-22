import hashlib
import json
import re
from urllib.parse import parse_qsl, urlsplit

from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


def validate_arxiv_page_request(request: SourcePageRequest) -> str:
    """Validate at the I/O boundary; a matching fingerprint is not authorization."""
    try:
        if not isinstance(request, SourcePageRequest):
            raise ValueError
        if request.source_id != "arxiv" or request.method != "GET":
            raise ValueError
        for fingerprint in (request.query_fingerprint, request.request_fingerprint):
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise ValueError
        if any(
            type(value) is not int
            for value in (request.start, request.max_results, request.maximum_window_results)
        ):
            raise ValueError
        if not (0 <= request.start < request.maximum_window_results <= 30000):
            raise ValueError
        if (
            not (1 <= request.max_results <= 2000)
            or request.start + request.max_results > request.maximum_window_results
        ):
            raise ValueError
        url = request.url
        if not isinstance(url, str) or len(url) > 200000 or any(ord(c) < 33 or ord(c) > 126 for c in url):
            raise ValueError
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc, parsed.path, parsed.fragment) != (
            "https",
            "export.arxiv.org",
            "/api/query",
            "",
        ):
            raise ValueError
        pairs = parse_qsl(
            parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=5, errors="strict"
        )
        parameters = dict(pairs)
        if len(pairs) != 5 or set(parameters) != {
            "search_query",
            "start",
            "max_results",
            "sortBy",
            "sortOrder",
        }:
            raise ValueError
        if (
            parameters["start"],
            parameters["max_results"],
            parameters["sortBy"],
            parameters["sortOrder"],
        ) != (str(request.start), str(request.max_results), "submittedDate", "ascending"):
            raise ValueError
        search = parameters["search_query"]
        if (
            not search.strip()
            or len(search.encode("utf-8")) > 16000
            or any(ord(c) < 32 or ord(c) == 127 for c in search)
        ):
            raise ValueError
        encoded = json.dumps(
            {"query": request.query_fingerprint, "method": "GET", "url": url},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != request.request_fingerprint:
            raise ValueError
        return parsed.path + "?" + parsed.query
    except (ValueError, TypeError, UnicodeError, AttributeError) as exc:
        raise SourceFetchError("invalid_source_request") from exc
