import hashlib
import json
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery, DeferredFilter
from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot
from libs.discovery.dtos.source_capabilities import SourceCapabilities
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.source_query_error import SourceQueryError


class ArxivQueryCompilerAdapter:
    """Pure provider translation. No HTTP, implicit filters, clocks, or database writes."""

    VERSION = "arxiv-query-v1"
    CAPABILITIES = SourceCapabilities(
        "arxiv",
        "arxiv-api-manual-20260923-v1",
        ("categories", "title_abstract_terms", "submittedDate"),
        ("languages", "free_only", "allow_preprints", "scope_text"),
        ("submittedDate",),
        3,
        1,
        2000,
        30000,
        False,
        False,
    )
    WARNINGS = (
        "submission_window_is_not_revision_feed",
        "offset_pagination_not_snapshot",
        "category_validation_syntax_only",
    )

    def describe(self) -> SourceCapabilities:
        return self.CAPABILITIES

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _identifier(value: str) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
            raise SourceQueryError("invalid_identifier")
        return value

    @staticmethod
    def _revision(value: int) -> int:
        if type(value) is not int or not 1 <= value < 2**63:
            raise SourceQueryError("invalid_revision")
        return value

    @staticmethod
    def _text(value: str, *, phrase: bool) -> str:
        if not isinstance(value, str):
            raise SourceQueryError("invalid_text")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError as exc:
            raise SourceQueryError("invalid_unicode") from exc
        limit = 512 if phrase else 20000
        if size > limit or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value):
            raise SourceQueryError("invalid_text")
        value = value.strip()
        if phrase and (not value or '"' in value or "\\" in value):
            # The provider manual does not define portable escaping inside phrases.
            raise SourceQueryError("unsupported_query_literal")
        return value

    def _terms(self, values: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(values, tuple) or len(values) > 100:
            raise SourceQueryError("invalid_terms")
        return tuple(sorted({self._text(value, phrase=True) for value in values}))

    def _sources(self, values: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(values, tuple) or len(values) > 100:
            raise SourceQueryError("invalid_sources")
        return tuple(sorted({self._identifier(value) for value in values}))

    @staticmethod
    def _time(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SourceQueryError("timezone_required")
        try:
            value = value.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise SourceQueryError("invalid_window") from exc
        if value.second or value.microsecond:
            raise SourceQueryError("unsupported_time_precision")
        return value

    def _input(self, query: SourceQueryInput) -> dict[str, Any]:
        if not isinstance(query, SourceQueryInput) or not isinstance(query.domain, DomainQuerySnapshot):
            raise SourceQueryError("invalid_query_input")
        if query.source_id != "arxiv":
            raise SourceQueryError("unsupported_source")
        if query.time_basis != "submittedDate":
            raise SourceQueryError("unsupported_time_basis")
        if not isinstance(query.deferred_mode, str) or query.deferred_mode not in {"reject", "defer"}:
            raise SourceQueryError("invalid_deferred_mode")
        if (
            type(query.page_size) is not int
            or not 1 <= query.page_size <= self.CAPABILITIES.maximum_page_size
        ):
            raise SourceQueryError("invalid_page_size")
        if type(query.free_only) is not bool or type(query.allow_preprints) is not bool:
            raise SourceQueryError("invalid_boolean")
        if (
            not isinstance(query.profile_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", query.profile_fingerprint) is None
        ):
            raise SourceQueryError("invalid_fingerprint")
        domain = query.domain
        sources, profile_sources = self._sources(domain.sources), self._sources(query.profile_sources)
        if "arxiv" not in sources or "arxiv" not in profile_sources:
            raise SourceQueryError("source_not_selected")
        if not isinstance(domain.categories, tuple) or len(domain.categories) > 100:
            raise SourceQueryError("invalid_category")
        for category in domain.categories:
            if (
                not isinstance(category, str)
                or re.fullmatch(r"[a-z][a-z0-9-]*(?:\.[A-Z]{2})?", category) is None
            ):
                raise SourceQueryError("invalid_category")
        start, end = self._time(query.window_start), self._time(query.window_end)
        if start >= end:
            raise SourceQueryError("invalid_window")
        return {
            "source_id": "arxiv",
            "profile_id": self._identifier(query.profile_id),
            "profile_revision": self._revision(query.profile_revision),
            "profile_fingerprint": query.profile_fingerprint,
            "domain": {
                "id": self._identifier(domain.domain_id),
                "revision": self._revision(domain.revision),
                "sources": sources,
                "categories": tuple(sorted(set(domain.categories))),
                "aliases": self._terms(domain.aliases),
                "include": self._terms(domain.include),
                "exclude": self._terms(domain.exclude),
            },
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "profile_sources": profile_sources,
            "profile_include": self._terms(query.profile_include),
            "profile_exclude": self._terms(query.profile_exclude),
            "languages": self._terms(query.languages),
            "free_only": query.free_only,
            "allow_preprints": query.allow_preprints,
            "scope_text": self._text(query.scope_text, phrase=False),
            "deferred_mode": query.deferred_mode,
            "time_basis": query.time_basis,
        }

    @staticmethod
    def _phrases(values: tuple[str, ...]) -> str:
        return "(" + " OR ".join(f'{field}:"{value}"' for value in values for field in ("ti", "abs")) + ")"

    @staticmethod
    def _timestamp(value: str) -> str:
        moment = datetime.fromisoformat(value)
        return f"{moment.year:04d}{moment.month:02d}{moment.day:02d}{moment.hour:02d}{moment.minute:02d}"

    def compile(self, query: SourceQueryInput) -> CompiledSourceQuery:
        data = self._input(query)
        domain = data["domain"]
        terms = tuple(sorted(set(domain["aliases"]) | set(domain["include"])))
        scope = []
        if domain["categories"]:
            scope.append("(" + " OR ".join("cat:" + item for item in domain["categories"]) + ")")
        if terms:
            scope.append(self._phrases(terms))
        if not scope:
            raise SourceQueryError("empty_discovery_scope")
        search = "(" + " OR ".join(scope) + ")"
        if data["profile_include"]:
            search += " AND " + self._phrases(data["profile_include"])
        exclusions = tuple(sorted(set(domain["exclude"]) | set(data["profile_exclude"])))
        if exclusions:
            search += " ANDNOT " + self._phrases(exclusions)
        start = self._timestamp(data["window_start"])
        end = self._timestamp(data["window_end"])
        search += f" AND submittedDate:[{start} TO {end}]"
        if len(search.encode("utf-8")) > 16000:
            raise SourceQueryError("query_too_large")
        deferred = []
        for name, stage, needed in (
            ("languages", "relevance", bool(data["languages"])),
            ("free_only", "access", data["free_only"]),
            ("allow_preprints", "publication_status", not data["allow_preprints"]),
            ("scope_text", "relevance", bool(data["scope_text"])),
        ):
            if needed:
                deferred.append(DeferredFilter(name, self._json(data[name]), stage))
        if deferred and query.deferred_mode == "reject":
            raise SourceQueryError("unsupported_filters", ",".join(item.name for item in deferred))
        provenance = self._json(
            {
                "compiler_version": self.VERSION,
                "capability_version": self.CAPABILITIES.version,
                "policy": "category-or-terms-v1",
                "window_semantics": "inclusive-minutes-utc",
                "input": data,
                "search_query": search,
                "page_size": query.page_size,
                "maximum_window_results": self.CAPABILITIES.maximum_window_results,
                "deferred_filters": [asdict(item) for item in deferred],
                "warnings": self.WARNINGS,
            }
        )
        return CompiledSourceQuery(
            "arxiv",
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

    def page(self, plan: CompiledSourceQuery, start: int = 0) -> SourcePageRequest:
        try:
            encoded = json.loads(plan.provenance_json)
            expected = {
                "compiler_version": plan.compiler_version,
                "capability_version": plan.capability_version,
                "search_query": plan.search_query,
                "page_size": plan.page_size,
                "maximum_window_results": plan.maximum_window_results,
                "deferred_filters": [asdict(item) for item in plan.deferred_filters],
                "warnings": list(plan.warnings),
            }
            valid = (
                plan.source_id == "arxiv"
                and plan.compiler_version == self.VERSION
                and plan.capability_version == self.CAPABILITIES.version
                and plan.maximum_window_results == self.CAPABILITIES.maximum_window_results
                and type(plan.page_size) is int
                and 1 <= plan.page_size <= self.CAPABILITIES.maximum_page_size
                and all(encoded[key] == value for key, value in expected.items())
                and self._hash(plan.provenance_json) == plan.query_fingerprint
            )
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise SourceQueryError("invalid_compiled_plan") from exc
        if not valid:
            raise SourceQueryError("invalid_compiled_plan")
        if type(start) is not int or not 0 <= start < plan.maximum_window_results:
            raise SourceQueryError("invalid_offset")
        size = min(plan.page_size, plan.maximum_window_results - start)
        parameters = (
            ("search_query", plan.search_query),
            ("start", str(start)),
            ("max_results", str(size)),
            ("sortBy", "submittedDate"),
            ("sortOrder", "ascending"),
        )
        url = "https://export.arxiv.org/api/query?" + urlencode(parameters)
        fingerprint = self._hash(self._json({"query": plan.query_fingerprint, "method": "GET", "url": url}))
        return SourcePageRequest(
            "arxiv", plan.query_fingerprint, fingerprint, "GET", url, start, size, plan.maximum_window_results
        )
