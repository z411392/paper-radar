import hashlib
import json
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import NoReturn
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.etree import ElementTree as ET

from libs.discovery.domain.services.evaluate_source_page import EvaluateSourcePage
from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery, DeferredFilter
from libs.discovery.dtos.pubmed_bibliography_batch import PubmedBibliographyBatch
from libs.discovery.dtos.pubmed_bibliography_record import PubmedBibliographyRecord
from libs.discovery.dtos.pubmed_search_page import PubmedSearchPage
from libs.discovery.dtos.source_capabilities import SourceCapabilities
from libs.discovery.dtos.source_page_observation import SourcePageObservation
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.source_parse_error import SourceParseError
from libs.discovery.exceptions.source_query_error import SourceQueryError


class _BoundedTreeBuilder(ET.TreeBuilder):
    def __init__(self, digest: str) -> None:
        super().__init__()
        self._digest = digest
        self._depth = 0
        self._nodes = 0

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self._depth += 1
        self._nodes += 1
        if self._depth > 40:
            raise SourceParseError("xml_depth_limit", self._digest)
        if self._nodes > 120000:
            raise SourceParseError("xml_node_limit", self._digest)
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        value = super().end(tag)
        self._depth -= 1
        return value

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        # PubMed eFetch XML legitimately declares the NLM PubMedArticle DTD.
        # Accept only the canonical NLM declaration family; ElementTree does
        # not need to fetch the external DTD for the document structure we use.
        valid_system = (
            isinstance(system, str)
            and re.fullmatch(
                r"https://dtd\.nlm\.nih\.gov/ncbi/pubmed/out/pubmed_[0-9]{6}\.dtd",
                system,
            )
            is not None
        )
        valid_pubid = (
            isinstance(pubid, str)
            and pubid.startswith("-//NLM//DTD PubMedArticle")
            and pubid.endswith("//EN")
        )
        if name == "PubmedArticleSet" and valid_system and valid_pubid:
            return
        raise SourceParseError("xml_doctype_forbidden", self._digest)


class PubmedSourceAdapter:
    VERSION = "pubmed-eutils-v1"
    PARSER_VERSION = "pubmed-eutils-parser-v1"
    CAPABILITIES = SourceCapabilities(
        "pubmed",
        "ncbi-eutils-20260924-v1",
        ("title_abstract_terms", "createDate", "languages"),
        ("free_only", "allow_preprints", "scope_text"),
        ("createDate",),
        1,
        1,
        200,
        10000,
        False,
        False,
    )
    WARNINGS = (
        "create_date_utc_day_window",
        "offset_pagination_not_snapshot",
        "bibliography_requires_efetch",
    )

    def __init__(
        self,
        *,
        tool: str,
        email: str,
        api_key: str | None = None,
    ) -> None:
        self._tool = self._token(tool, "invalid_ncbi_tool", 64)
        self._email = self._email_value(email)
        self._api_key = None if api_key is None else self._token(
            api_key,
            "invalid_ncbi_api_key",
            128,
        )

    def describe(self) -> SourceCapabilities:
        return self.CAPABILITIES

    @staticmethod
    def _json(value: object) -> str:
        try:
            return json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise SourceQueryError("invalid_query_input") from exc

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _token(value: object, code: str, maximum: int) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > maximum
            or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in value)
        ):
            raise SourceQueryError(code)
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            raise SourceQueryError(code) from None
        return value

    @classmethod
    def _email_value(cls, value: object) -> str:
        text = cls._token(value, "invalid_ncbi_email", 254)
        if text.count("@") != 1 or text.startswith("@") or text.endswith("@"):
            raise SourceQueryError("invalid_ncbi_email")
        return text

    @staticmethod
    def _text(value: object, code: str, *, maximum_bytes: int = 512) -> str:
        if not isinstance(value, str):
            raise SourceQueryError(code)
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise SourceQueryError(code) from None
        text = value.strip()
        if (
            not text
            or size > maximum_bytes
            or '"' in text
            or "\\" in text
            or any(unicodedata.category(char) in {"Cc", "Cs"} for char in text)
        ):
            raise SourceQueryError(code)
        return text

    @classmethod
    def _terms(cls, values: object, code: str = "invalid_terms") -> tuple[str, ...]:
        if not isinstance(values, tuple) or len(values) > 100:
            raise SourceQueryError(code)
        return tuple(sorted({cls._text(value, code) for value in values}))

    @staticmethod
    def _time(value: object) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SourceQueryError("timezone_required")
        try:
            result = value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise SourceQueryError("invalid_window") from None
        if result.hour or result.minute or result.second or result.microsecond:
            raise SourceQueryError("unsupported_time_precision")
        return result

    @staticmethod
    def _date(value: datetime) -> str:
        return value.strftime("%Y/%m/%d")

    @staticmethod
    def _phrase(value: str) -> str:
        return f'"{value}"[Title/Abstract]'

    def compile(self, query: SourceQueryInput) -> CompiledSourceQuery:
        if not isinstance(query, SourceQueryInput):
            raise SourceQueryError("invalid_query_input")
        if query.source_id != "pubmed":
            raise SourceQueryError("unsupported_source")
        if query.time_basis != "createDate":
            raise SourceQueryError("unsupported_time_basis")
        if query.deferred_mode not in {"reject", "defer"}:
            raise SourceQueryError("invalid_deferred_mode")
        if (
            type(query.page_size) is not int
            or not 1 <= query.page_size <= self.CAPABILITIES.maximum_page_size
        ):
            raise SourceQueryError("invalid_page_size")
        if (
            not isinstance(query.profile_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", query.profile_fingerprint) is None
        ):
            raise SourceQueryError("invalid_fingerprint")
        if "pubmed" not in query.domain.sources or "pubmed" not in query.profile_sources:
            raise SourceQueryError("source_not_selected")

        start, end = self._time(query.window_start), self._time(query.window_end)
        if start >= end:
            raise SourceQueryError("invalid_window")
        if (end - start).total_seconds() % 86400 != 0:
            raise SourceQueryError("unsupported_time_precision")

        domain_terms = tuple(
            sorted(
                set(self._terms(query.domain.aliases))
                | set(self._terms(query.domain.include))
            )
        )
        if not domain_terms:
            raise SourceQueryError("empty_discovery_scope")
        search = "(" + " OR ".join(self._phrase(item) for item in domain_terms) + ")"
        profile_include = self._terms(query.profile_include)
        if profile_include:
            search += " AND (" + " OR ".join(
                self._phrase(item) for item in profile_include
            ) + ")"
        exclusions = tuple(
            sorted(
                set(self._terms(query.domain.exclude))
                | set(self._terms(query.profile_exclude))
            )
        )
        if exclusions:
            search += " NOT (" + " OR ".join(self._phrase(item) for item in exclusions) + ")"
        languages = self._terms(query.languages, "invalid_language")
        if languages:
            search += " AND (" + " OR ".join(
                f'"{item}"[Language]' for item in languages
            ) + ")"
        if len(search.encode("utf-8")) > 16000:
            raise SourceQueryError("query_too_large")

        deferred = []
        for name, value, stage, needed in (
            ("free_only", query.free_only, "access", query.free_only),
            (
                "allow_preprints",
                query.allow_preprints,
                "publication_status",
                not query.allow_preprints,
            ),
            ("scope_text", query.scope_text, "relevance", bool(query.scope_text)),
        ):
            if needed:
                deferred.append(
                    DeferredFilter(
                        name,
                        self._json(value),
                        stage,
                    )
                )
        if deferred and query.deferred_mode == "reject":
            raise SourceQueryError(
                "unsupported_filters",
                ",".join(item.name for item in deferred),
            )

        data = {
            "source_id": "pubmed",
            "profile_id": query.profile_id,
            "profile_revision": query.profile_revision,
            "profile_fingerprint": query.profile_fingerprint,
            "domain": {
                "id": query.domain.domain_id,
                "revision": query.domain.revision,
                "sources": list(query.domain.sources),
                "aliases": list(query.domain.aliases),
                "include": list(query.domain.include),
                "exclude": list(query.domain.exclude),
            },
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "provider_mindate": self._date(start),
            "provider_maxdate": self._date(end - timedelta(days=1)),
            "search_query": search,
            "page_size": query.page_size,
            "deferred_filters": [asdict(item) for item in deferred],
            "warnings": list(self.WARNINGS),
        }
        provenance = self._json(
            {
                "compiler_version": self.VERSION,
                "capability_version": self.CAPABILITIES.version,
                "policy": "title-abstract-crdt-v1",
                "input": data,
            }
        )
        return CompiledSourceQuery(
            "pubmed",
            self.VERSION,
            self.CAPABILITIES.version,
            self._hash(provenance),
            provenance,
            search,
            query.page_size,
            self.CAPABILITIES.maximum_window_results,
            tuple(deferred),
            self.WARNINGS,
        )

    def _common_parameters(self) -> list[tuple[str, str]]:
        values = [("tool", self._tool), ("email", self._email)]
        if self._api_key is not None:
            values.append(("api_key", self._api_key))
        return values

    def page(self, plan: CompiledSourceQuery, start: int = 0) -> SourcePageRequest:
        try:
            data = json.loads(plan.provenance_json)
            inner = data["input"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise SourceQueryError("invalid_compiled_plan") from None
        if (
            plan.source_id != "pubmed"
            or plan.compiler_version != self.VERSION
            or plan.capability_version != self.CAPABILITIES.version
            or self._hash(plan.provenance_json) != plan.query_fingerprint
            or inner.get("search_query") != plan.search_query
            or inner.get("page_size") != plan.page_size
        ):
            raise SourceQueryError("invalid_compiled_plan")
        if type(start) is not int or not 0 <= start < plan.maximum_window_results:
            raise SourceQueryError("invalid_offset")
        size = min(plan.page_size, plan.maximum_window_results - start)
        parameters = [
            ("db", "pubmed"),
            ("term", plan.search_query),
            ("retmode", "json"),
            ("retstart", str(start)),
            ("retmax", str(size)),
            ("datetype", "crdt"),
            ("mindate", inner["provider_mindate"]),
            ("maxdate", inner["provider_maxdate"]),
            *self._common_parameters(),
        ]
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
            + urlencode(parameters)
        )
        fingerprint = self._hash(
            self._json(
                {
                    "query": plan.query_fingerprint,
                    "method": "GET",
                    "url": url,
                }
            )
        )
        return SourcePageRequest(
            "pubmed",
            plan.query_fingerprint,
            fingerprint,
            "GET",
            url,
            start,
            size,
            plan.maximum_window_results,
        )

    @staticmethod
    def _strict_json(body: bytes, digest: str) -> dict[str, object]:
        if not isinstance(body, bytes) or len(body) > 2_000_000:
            raise SourceParseError("response_too_large", digest)

        def pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in rows:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            data = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise SourceParseError("malformed_json", digest) from None
        if not isinstance(data, dict):
            raise SourceParseError("malformed_json", digest)
        return data

    @staticmethod
    def _count(value: object, digest: str) -> int:
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,10}", value) is None:
            raise SourceParseError("invalid_page_counts", digest)
        return int(value)

    def parse_search(
        self,
        request: SourcePageRequest,
        body: bytes,
        *,
        http_status: int,
    ) -> PubmedSearchPage:
        digest = hashlib.sha256(body).hexdigest() if isinstance(body, bytes) else ""
        if not isinstance(request, SourcePageRequest) or request.source_id != "pubmed":
            raise SourceParseError("invalid_page_request", digest)
        if http_status != 200:
            raise SourceParseError("http_response_not_successful", digest)
        data = self._strict_json(body, digest)
        result = data.get("esearchresult")
        if not isinstance(result, dict):
            raise SourceParseError("invalid_pubmed_search", digest)
        total = self._count(result.get("count"), digest)
        retmax = self._count(result.get("retmax"), digest)
        retstart = self._count(result.get("retstart"), digest)
        ids = result.get("idlist")
        if (
            not isinstance(ids, list)
            or len(ids) > request.max_results
            or any(
                not isinstance(item, str)
                or re.fullmatch(r"[0-9]{1,10}", item) is None
                or int(item) <= 0
                for item in ids
            )
            or len(ids) != len(set(ids))
            or retmax < len(ids)
        ):
            raise SourceParseError("invalid_pubmed_search", digest)
        pmids = tuple(str(int(item)) for item in ids)
        observation = SourcePageObservation(
            "pubmed",
            request.query_fingerprint,
            retstart,
            total,
            pmids,
        )
        try:
            EvaluateSourcePage()(request, observation)
        except SourceQueryError as exc:
            raise SourceParseError(exc.code, digest) from None
        return PubmedSearchPage(
            observation,
            pmids,
            body,
            digest,
            request.request_fingerprint,
            self.PARSER_VERSION,
        )

    def bibliography_request(self, pmids: tuple[str, ...]) -> SourcePageRequest:
        if (
            not isinstance(pmids, tuple)
            or not 1 <= len(pmids) <= 200
            or len(pmids) != len(set(pmids))
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9]{1,10}", value) is None
                or int(value) <= 0
                for value in pmids
            )
        ):
            raise SourceQueryError("invalid_pubmed_ids")
        normalized = tuple(str(int(value)) for value in pmids)
        query_fingerprint = self._hash(self._json({"pmids": normalized}))
        parameters = [
            ("db", "pubmed"),
            ("id", ",".join(normalized)),
            ("retmode", "xml"),
            *self._common_parameters(),
        ]
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
            + urlencode(parameters)
        )
        request_fingerprint = self._hash(
            self._json({"query": query_fingerprint, "method": "GET", "url": url})
        )
        return SourcePageRequest(
            "pubmed",
            query_fingerprint,
            request_fingerprint,
            "GET",
            url,
            0,
            len(normalized),
            len(normalized),
        )

    @staticmethod
    def _flatten(node: ET.Element | None) -> str | None:
        if node is None:
            return None
        text = " ".join("".join(node.itertext()).split())
        return text or None

    @classmethod
    def _article_id(
        cls,
        article: ET.Element,
        kind: str,
        digest: str,
    ) -> str | None:
        values = []
        for node in article.findall("./PubmedData/ArticleIdList/ArticleId"):
            if node.get("IdType") == kind:
                value = cls._flatten(node)
                if value:
                    values.append(value)
        if len(values) > 1:
            raise SourceParseError("duplicate_field", digest)
        return values[0] if values else None

    @classmethod
    def _publication_date(
        cls,
        article: ET.Element,
    ) -> tuple[str | None, str | None]:
        node = article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate")
        if node is None:
            return None, None
        medline = cls._flatten(node.find("MedlineDate"))
        if medline is not None:
            return medline, "provider_text"
        year = cls._flatten(node.find("Year"))
        month = cls._flatten(node.find("Month"))
        day = cls._flatten(node.find("Day"))
        if year is None:
            return None, None
        parts = [year]
        precision = "year"
        if month is not None:
            parts.append(month)
            precision = "month"
        if day is not None:
            parts.append(day)
            precision = "day"
        return "-".join(parts), precision

    def parse_bibliography(
        self,
        request: SourcePageRequest,
        body: bytes,
        *,
        http_status: int,
    ) -> PubmedBibliographyBatch:
        digest = hashlib.sha256(body).hexdigest() if isinstance(body, bytes) else ""
        if (
            not isinstance(request, SourcePageRequest)
            or request.source_id != "pubmed"
            or request.start != 0
            or not 1 <= request.max_results <= 200
        ):
            raise SourceParseError("invalid_page_request", digest)
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
        if root.tag != "PubmedArticleSet":
            raise SourceParseError("invalid_pubmed_bibliography", digest)

        requested = parse_qs(urlsplit(request.url).query).get("id", [""])[0].split(",")
        expected = tuple(str(int(value)) for value in requested if value)
        records = []
        seen = set()
        for article in root.findall("PubmedArticle"):
            pmid = self._flatten(article.find("./MedlineCitation/PMID"))
            if (
                pmid is None
                or re.fullmatch(r"[0-9]{1,10}", pmid) is None
                or int(pmid) <= 0
            ):
                raise SourceParseError("invalid_pubmed_identifier", digest)
            pmid = str(int(pmid))
            if pmid in seen:
                raise SourceParseError("duplicate_page_identity", digest)
            seen.add(pmid)
            title = self._flatten(article.find("./MedlineCitation/Article/ArticleTitle"))
            if title is None:
                raise SourceParseError("missing_field", digest)
            abstract_parts = []
            for node in article.findall("./MedlineCitation/Article/Abstract/AbstractText"):
                text = self._flatten(node)
                if text is None:
                    continue
                label = node.get("Label")
                abstract_parts.append(f"{label}: {text}" if label else text)
            authors = []
            for node in article.findall("./MedlineCitation/Article/AuthorList/Author"):
                collective = self._flatten(node.find("CollectiveName"))
                if collective is not None:
                    authors.append(collective)
                    continue
                last = self._flatten(node.find("LastName"))
                fore = self._flatten(node.find("ForeName")) or self._flatten(node.find("Initials"))
                name = " ".join(part for part in (last, fore) if part)
                if name:
                    authors.append(name)
            doi = self._article_id(article, "doi", digest)
            pmcid = self._article_id(article, "pmc", digest)
            if doi is not None:
                doi = doi.lower()
            if pmcid is not None:
                pmcid = pmcid.upper()
                if re.fullmatch(r"PMC[0-9]{1,10}", pmcid) is None:
                    raise SourceParseError("invalid_pmc_identifier", digest)
            publication_date, precision = self._publication_date(article)
            languages = tuple(
                sorted(
                    {
                        value
                        for node in article.findall("./MedlineCitation/Article/Language")
                        if (value := self._flatten(node)) is not None
                    }
                )
            )
            publication_types = tuple(
                value
                for node in article.findall(
                    "./MedlineCitation/Article/PublicationTypeList/PublicationType"
                )
                if (value := self._flatten(node)) is not None
            )
            records.append(
                PubmedBibliographyRecord(
                    pmid,
                    title,
                    "\n".join(abstract_parts) if abstract_parts else None,
                    tuple(authors),
                    self._flatten(
                        article.find("./MedlineCitation/Article/Journal/Title")
                    ),
                    publication_date,
                    precision,
                    doi,
                    pmcid,
                    languages,
                    publication_types,
                )
            )
        if set(seen) != set(expected) or len(records) != len(expected):
            raise SourceParseError("pubmed_batch_identity_mismatch", digest)
        return PubmedBibliographyBatch(
            tuple(records),
            body,
            digest,
            request.request_fingerprint,
            self.PARSER_VERSION,
        )
