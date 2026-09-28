import hashlib
import re
from datetime import datetime, timezone
from typing import NoReturn
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from libs.discovery.domain.services.evaluate_source_page import EvaluateSourcePage
from libs.discovery.dtos.arxiv_source_record import ArxivSourceRecord
from libs.discovery.dtos.parsed_arxiv_page import ParsedArxivPage
from libs.discovery.dtos.source_page_observation import SourcePageObservation
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_parse_error import SourceParseError
from libs.discovery.exceptions.source_query_error import SourceQueryError

ATOM = "{http://www.w3.org/2005/Atom}"
SEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
ARXIV = "{http://arxiv.org/schemas/atom}"
PARSER_VERSION = "arxiv-atom-v1"
MAX_BODY_BYTES = 8_000_000
MAX_XML_DEPTH = 32
MAX_XML_NODES = 100_000


class _BoundedTreeBuilder(ET.TreeBuilder):
    def __init__(self, digest: str) -> None:
        super().__init__()
        self._digest = digest
        self._depth = 0
        self._nodes = 0

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self._depth += 1
        self._nodes += 1
        if self._depth > MAX_XML_DEPTH:
            raise SourceParseError("xml_depth_limit", self._digest)
        if self._nodes > MAX_XML_NODES:
            raise SourceParseError("xml_node_limit", self._digest)
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        value = super().end(tag)
        self._depth -= 1
        return value

    def doctype(self, name: str, pubid: str | None, system: str | None) -> NoReturn:
        raise SourceParseError("xml_doctype_forbidden", self._digest)


class ArxivAtomParserAdapter:
    """Decode bounded response bytes. No network, persistence, eligibility or inferred publication status."""

    def __call__(self, request: SourcePageRequest, body: bytes, *, http_status: int) -> ParsedArxivPage:
        if not isinstance(body, bytes):
            raise SourceParseError("invalid_response_bytes")
        if len(body) > MAX_BODY_BYTES:
            raise SourceParseError("response_too_large")
        digest = hashlib.sha256(body).hexdigest()
        if type(http_status) is not int or http_status != 200:
            raise SourceParseError("http_response_not_successful", digest)
        self._request(request, digest)
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise SourceParseError("invalid_utf8", digest) from None
        declaration = re.match(r"\ufeff?<\?xml\s[^?]*encoding\s*=\s*['\"]([^'\"]+)", text, re.I)
        if declaration is not None and declaration[1].lower() not in {"utf-8", "utf8"}:
            raise SourceParseError("unsupported_xml_encoding", digest)
        try:
            root = ET.fromstring(text, parser=ET.XMLParser(target=_BoundedTreeBuilder(digest)))
        except (ET.ParseError, ValueError):
            raise SourceParseError("malformed_xml", digest) from None
        if root.tag != ATOM + "feed":
            raise SourceParseError("invalid_feed_namespace", digest)
        if any(child.tag.rsplit("}", 1)[-1] == "entry" and child.tag != ATOM + "entry" for child in root):
            raise SourceParseError("invalid_entry_namespace", digest)
        entries = root.findall(ATOM + "entry")
        for entry in entries:
            identity = self._text(entry, ATOM + "id", digest, required=True)
            if re.match(r"https?://arxiv\.org/api/errors(?:[#?]|$)", identity or ""):
                raise SourceParseError("arxiv_api_error", digest)
        total = self._count(root, "totalResults", digest)
        start = self._count(root, "startIndex", digest)
        items = self._count(root, "itemsPerPage", digest)
        if items > request.max_results or len(entries) > items or (entries and items == 0):
            raise SourceParseError("invalid_page_counts", digest)
        records = tuple(self._record(entry, digest) for entry in entries)
        observation = SourcePageObservation(
            "arxiv",
            request.query_fingerprint,
            start,
            total,
            tuple(record.source_record_id for record in records),
        )
        try:
            EvaluateSourcePage()(request, observation)
        except SourceQueryError as exc:
            raise SourceParseError(exc.code, digest) from None
        feed_updated = self._text(root, ATOM + "updated", digest)
        return ParsedArxivPage(
            observation,
            records,
            body,
            digest,
            request.request_fingerprint,
            PARSER_VERSION,
            self._text(root, ATOM + "id", digest),
            self._timestamp(feed_updated, digest) if feed_updated is not None else None,
            items,
        )

    @staticmethod
    def _request(request: SourcePageRequest, digest: str) -> None:
        if not isinstance(request, SourcePageRequest):
            raise SourceParseError("invalid_page_request", digest)
        counts = (request.start, request.max_results, request.maximum_window_results)
        fingerprints = (request.query_fingerprint, request.request_fingerprint)
        if (
            request.source_id != "arxiv"
            or request.method != "GET"
            or any(type(n) is not int or n < 0 for n in counts)
            or not 1 <= request.max_results <= 2000
            or not 1 <= request.maximum_window_results <= 30000
            or request.start > request.maximum_window_results
            or any(not isinstance(s, str) or re.fullmatch(r"[0-9a-f]{64}", s) is None for s in fingerprints)
        ):
            raise SourceParseError("invalid_page_request", digest)

    @staticmethod
    def _one(parent: ET.Element, tag: str, digest: str, *, required: bool = False) -> ET.Element | None:
        items = parent.findall(tag)
        if len(items) > 1:
            raise SourceParseError("duplicate_field", digest)
        if not items:
            if required:
                raise SourceParseError("missing_field", digest)
            return None
        return items[0]

    def _text(
        self,
        parent: ET.Element,
        tag: str,
        digest: str,
        *,
        required: bool = False,
        preserve_space: bool = False,
    ) -> str | None:
        node = self._one(parent, tag, digest, required=required)
        if node is None:
            return None
        if len(node):
            raise SourceParseError("unsupported_text_markup", digest)
        text = node.text or ""
        if required and not text.strip():
            raise SourceParseError("empty_required_field", digest)
        return text if preserve_space else text.strip()

    def _count(self, root: ET.Element, name: str, digest: str) -> int:
        value = self._text(root, SEARCH + name, digest, required=True)
        if value is None or not re.fullmatch(r"[0-9]{1,10}", value):
            raise SourceParseError("invalid_page_counts", digest)
        return int(value)

    @staticmethod
    def _timestamp(value: str | None, digest: str) -> datetime:
        if value is None or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value
        ):
            raise SourceParseError("invalid_timestamp", digest)
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise SourceParseError("invalid_timestamp", digest)
        try:
            return datetime.fromisoformat(value).astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise SourceParseError("invalid_timestamp", digest) from None

    @staticmethod
    def _identity(value: str, digest: str) -> tuple[str, str, int | None]:
        if any(ord(char) <= 32 or ord(char) == 127 for char in value):
            raise SourceParseError("invalid_source_identity", digest)
        try:
            url = urlsplit(value)
        except ValueError:
            raise SourceParseError("invalid_source_identity", digest) from None
        if (
            url.scheme not in {"http", "https"}
            or url.netloc != "arxiv.org"
            or url.query
            or url.fragment
            or not url.path.startswith("/abs/")
        ):
            raise SourceParseError("invalid_source_identity", digest)
        identity = url.path[len("/abs/") :]
        match = re.fullmatch(
            r"((?:[0-9]{4}\.[0-9]{4,5}|[a-z][A-Za-z0-9.-]*/[0-9]{7}))(?:v([1-9][0-9]{0,8}))?", identity
        )
        if match is None:
            raise SourceParseError("invalid_source_identity", digest)
        base = match[1]
        serial = base.rsplit("/", 1)[-1]
        if not 1 <= int(serial[2:4]) <= 12:
            raise SourceParseError("invalid_source_identity", digest)
        return identity, base, int(match[2]) if match[2] else None

    def _record(self, entry: ET.Element, digest: str) -> ArxivSourceRecord:
        source_url = self._text(entry, ATOM + "id", digest, required=True)
        assert source_url is not None
        identity, base, version = self._identity(source_url, digest)
        source_url = "https://arxiv.org/abs/" + identity
        title = self._text(entry, ATOM + "title", digest, required=True)
        assert title is not None
        abstract = self._text(entry, ATOM + "summary", digest, preserve_space=True)
        authors = []
        for author in entry.findall(ATOM + "author"):
            name = self._text(author, ATOM + "name", digest, required=True)
            assert name is not None
            affiliations = []
            for affiliation in author.findall(ARXIV + "affiliation"):
                if len(affiliation):
                    raise SourceParseError("unsupported_text_markup", digest)
                affiliations.append((affiliation.text or "").strip())
            authors.append((name, tuple(affiliations)))
        categories = []
        for category in entry.findall(ATOM + "category"):
            term = category.get("term", "").strip()
            if not term:
                raise SourceParseError("invalid_category", digest)
            categories.append((term, category.get("scheme")))
        if not authors or not categories:
            raise SourceParseError("missing_field", digest)
        primary = self._one(entry, ARXIV + "primary_category", digest)
        primary_term = None if primary is None else primary.get("term", "").strip()
        if primary_term is not None and primary_term not in {term for term, _ in categories}:
            raise SourceParseError("invalid_primary_category", digest)
        links = []
        for link in entry.findall(ATOM + "link"):
            href = link.get("href")
            if href is None or not href.strip():
                raise SourceParseError("invalid_link_metadata", digest)
            links.append((href, link.get("rel"), link.get("title"), link.get("type")))
        return ArxivSourceRecord(
            identity,
            source_url,
            base,
            version,
            title,
            abstract,
            tuple(authors),
            self._timestamp(self._text(entry, ATOM + "published", digest, required=True), digest),
            self._timestamp(self._text(entry, ATOM + "updated", digest, required=True), digest),
            tuple(categories),
            primary_term,
            self._text(entry, ARXIV + "doi", digest),
            self._text(entry, ARXIV + "journal_ref", digest),
            self._text(entry, ARXIV + "comment", digest),
            tuple(links),
            ("abstract",) if abstract is None else (),
        )
