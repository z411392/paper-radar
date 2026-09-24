import hashlib
import re
from typing import NoReturn
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.etree import ElementTree as ET

from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_parse_error import SourceParseError
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.dtos.pmc_full_text_capability import PmcFullTextCapability
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class _BoundedTreeBuilder(ET.TreeBuilder):
    def __init__(self, digest: str) -> None:
        super().__init__()
        self._digest = digest
        self._depth = 0
        self._nodes = 0

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self._depth += 1
        self._nodes += 1
        if self._depth > 64:
            raise SourceParseError("xml_depth_limit", self._digest)
        if self._nodes > 250000:
            raise SourceParseError("xml_node_limit", self._digest)
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        value = super().end(tag)
        self._depth -= 1
        return value

    def doctype(self, name: str, pubid: str | None, system: str | None) -> NoReturn:
        raise SourceParseError("xml_doctype_forbidden", self._digest)


class PmcFullTextAdapter:
    VERSION = "pmc-oai-v1"
    BASE_URL = "https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/"
    OAI = "{http://www.openarchives.org/OAI/2.0/}"
    XLINK = "{http://www.w3.org/1999/xlink}href"

    @staticmethod
    def _hash(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def request(self, pmcid: str) -> SourcePageRequest:
        try:
            normalized = NormalizePaperIdentifier()("pmc", pmcid)
        except PaperIdentityError as exc:
            raise SourceParseError("invalid_pmc_identifier") from exc
        identifier = "oai:pubmedcentral.nih.gov:" + normalized.normalized_value
        parameters = (
            ("verb", "GetRecord"),
            ("identifier", identifier),
            ("metadataPrefix", "pmc"),
        )
        url = self.BASE_URL + "?" + urlencode(parameters)
        query_fingerprint = hashlib.sha256(
            ("pmc-oai-getrecord-v1\0" + normalized.normalized_value).encode()
        ).hexdigest()
        request_fingerprint = hashlib.sha256(
            (query_fingerprint + "\0GET\0" + url).encode()
        ).hexdigest()
        return SourcePageRequest(
            "pmc-oai",
            query_fingerprint,
            request_fingerprint,
            "GET",
            url,
            0,
            1,
            1,
        )

    @classmethod
    def _request_pmcid(cls, request: SourcePageRequest, digest: str) -> str:
        if (
            not isinstance(request, SourcePageRequest)
            or request.source_id != "pmc-oai"
            or request.method != "GET"
            or request.start != 0
            or request.max_results != 1
            or request.maximum_window_results != 1
        ):
            raise SourceParseError("invalid_page_request", digest)
        parsed = urlsplit(request.url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "pmc.ncbi.nlm.nih.gov"
            or parsed.path != "/api/oai/v1/mh/"
            or parsed.fragment
        ):
            raise SourceParseError("invalid_page_request", digest)
        query = parse_qs(parsed.query)
        if set(query) != {"verb", "identifier", "metadataPrefix"}:
            raise SourceParseError("invalid_page_request", digest)
        if query["verb"] != ["GetRecord"] or query["metadataPrefix"] != ["pmc"]:
            raise SourceParseError("invalid_page_request", digest)
        values = query["identifier"]
        if len(values) != 1 or not values[0].startswith("oai:pubmedcentral.nih.gov:"):
            raise SourceParseError("invalid_page_request", digest)
        raw = values[0].rsplit(":", 1)[-1]
        try:
            return NormalizePaperIdentifier()("pmc", raw).normalized_value
        except PaperIdentityError:
            raise SourceParseError("invalid_page_request", digest) from None

    @staticmethod
    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    @classmethod
    def _children(cls, node: ET.Element, name: str) -> list[ET.Element]:
        return [child for child in list(node) if cls._local(child.tag) == name]

    @classmethod
    def _first_descendant(
        cls,
        node: ET.Element,
        name: str,
    ) -> ET.Element | None:
        for child in node.iter():
            if cls._local(child.tag) == name:
                return child
        return None

    @staticmethod
    def _text(node: ET.Element | None) -> str | None:
        if node is None:
            return None
        value = " ".join("".join(node.itertext()).split())
        return value or None

    @staticmethod
    def _usage_class(url: str | None) -> str:
        if url is None:
            return "unknown"
        lowered = url.lower().rstrip("/") + "/"
        patterns = (
            ("/publicdomain/zero/", "cc0"),
            ("/licenses/by-nc-nd/", "cc-by-nc-nd"),
            ("/licenses/by-nc-sa/", "cc-by-nc-sa"),
            ("/licenses/by-nc/", "cc-by-nc"),
            ("/licenses/by-nd/", "cc-by-nd"),
            ("/licenses/by-sa/", "cc-by-sa"),
            ("/licenses/by/", "cc-by"),
        )
        for needle, value in patterns:
            if needle in lowered:
                return value
        return "unknown"

    def parse(
        self,
        request: SourcePageRequest,
        body: bytes,
        *,
        http_status: int,
    ) -> PmcFullTextCapability:
        digest = self._hash(body) if isinstance(body, bytes) else ""
        pmcid = self._request_pmcid(request, digest)
        if http_status != 200:
            raise SourceParseError("http_response_not_successful", digest)
        if not isinstance(body, bytes) or len(body) > 8_000_000:
            raise SourceParseError("response_too_large", digest)
        try:
            root = ET.fromstring(
                body,
                parser=ET.XMLParser(target=_BoundedTreeBuilder(digest)),
            )
        except (ET.ParseError, ValueError):
            raise SourceParseError("malformed_xml", digest) from None
        if root.tag != self.OAI + "OAI-PMH":
            raise SourceParseError("invalid_pmc_oai", digest)

        errors = self._children(root, "error")
        if errors:
            if len(errors) != 1:
                raise SourceParseError("invalid_pmc_oai", digest)
            code = errors[0].get("code")
            if code in {"idDoesNotExist", "cannotDisseminateFormat", "noRecordsMatch"}:
                return PmcFullTextCapability(
                    pmcid,
                    False,
                    "unknown",
                    None,
                    None,
                    "unavailable",
                    None,
                    digest,
                    "pmc_full_text_unavailable",
                )
            raise SourceParseError("pmc_oai_error", digest)

        get_record = self._first_descendant(root, "GetRecord")
        record = None if get_record is None else self._first_descendant(get_record, "record")
        if record is None:
            raise SourceParseError("invalid_pmc_oai", digest)
        header = self._first_descendant(record, "header")
        metadata = self._first_descendant(record, "metadata")
        if header is None or metadata is None:
            raise SourceParseError("invalid_pmc_oai", digest)
        identifier = self._text(self._first_descendant(header, "identifier"))
        if identifier != "oai:pubmedcentral.nih.gov:" + pmcid:
            raise SourceParseError("pmc_identity_mismatch", digest)
        sets = [
            self._text(node)
            for node in header
            if self._local(node.tag) == "setSpec"
        ]
        if "pmc-open" not in sets:
            raise SourceParseError("pmc_full_text_not_reusable_set", digest)

        article = self._first_descendant(metadata, "article")
        if article is None:
            raise SourceParseError("pmc_full_text_missing", digest)
        article_pmc = None
        for node in article.iter():
            if self._local(node.tag) == "article-id" and node.get("pub-id-type") == "pmc":
                article_pmc = self._text(node)
                break
        if article_pmc is None or article_pmc.upper() != pmcid:
            raise SourceParseError("pmc_identity_mismatch", digest)

        license_node = self._first_descendant(article, "license")
        license_url = None
        license_text = None
        if license_node is not None:
            raw_url = license_node.get(self.XLINK) or license_node.get("href")
            if raw_url is not None:
                if (
                    not isinstance(raw_url, str)
                    or len(raw_url) > 2048
                    or not raw_url.startswith("https://")
                ):
                    raise SourceParseError("invalid_pmc_license", digest)
                license_url = raw_url
            license_text = self._text(license_node)
        return PmcFullTextCapability(
            pmcid,
            True,
            "permitted",
            license_url,
            license_text,
            self._usage_class(license_url),
            body,
            digest,
            None,
        )
