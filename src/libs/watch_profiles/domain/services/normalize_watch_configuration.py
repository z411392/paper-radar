import hashlib
import json
import re
from typing import Any

from libs.watch_profiles.dtos.configuration_document import ConfigurationDocument
from libs.watch_profiles.exceptions.watch_configuration_error import WatchConfigurationError


class NormalizeWatchConfiguration:
    """Validate the import grammar; registered source IDs do not imply live capability."""

    def __init__(self, source_ids: frozenset[str]) -> None:
        self._source_ids = source_ids

    def __call__(self, kind: str, payload: str) -> ConfigurationDocument:
        if not isinstance(payload, str):
            raise WatchConfigurationError("invalid_document", "expected JSON text")
        try:
            size = len(payload.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise WatchConfigurationError("invalid_unicode") from exc
        if size > 1_000_000:
            raise WatchConfigurationError("invalid_document", "expected bounded JSON text")
        try:
            raw = json.loads(payload, object_pairs_hook=self._unique_keys, parse_constant=self._constant)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise WatchConfigurationError("invalid_json") from exc
        if kind == "domains":
            self._keys(raw, {"domains"})
            items = raw["domains"]
            if not isinstance(items, list) or not items:
                raise WatchConfigurationError("invalid_domains")
            values = [self._domain(item) for item in items]
            ids = [item["id"] for item in values]
            if len(ids) != len(set(ids)):
                raise WatchConfigurationError("duplicate_domain")
            result = {"domains": sorted(values, key=lambda item: item["id"])}
        elif kind == "profile":
            result = self._profile(raw)
        else:
            raise WatchConfigurationError("invalid_document_kind", kind)
        canonical = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        fingerprint = hashlib.sha256(
            ("watch-config-v1:" + kind + ":" + canonical).encode("utf-8")
        ).hexdigest()
        return ConfigurationDocument(kind, canonical, fingerprint)

    @staticmethod
    def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WatchConfigurationError("duplicate_key", key)
            result[key] = value
        return result

    @staticmethod
    def _constant(value: str) -> None:
        raise WatchConfigurationError("invalid_number", value)

    @staticmethod
    def _keys(value: Any, expected: set[str]) -> None:
        if not isinstance(value, dict) or set(value) != expected:
            raise WatchConfigurationError("invalid_fields")

    @staticmethod
    def _text(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise WatchConfigurationError("invalid_text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise WatchConfigurationError("invalid_unicode") from exc
        return value.strip()

    def _identifier(self, value: Any) -> str:
        value = self._text(value)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
            raise WatchConfigurationError("invalid_identifier")
        return value

    def _strings(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise WatchConfigurationError("invalid_list")
        items = [self._text(item) for item in value]
        if len(items) != len(set(items)):
            raise WatchConfigurationError("duplicate_value")
        return sorted(items)

    def _sources(self, value: Any) -> list[str]:
        items = self._strings(value)
        if not items or not set(items) <= self._source_ids:
            raise WatchConfigurationError("unknown_source")
        return items

    def _domain(self, raw: Any) -> dict[str, Any]:
        self._keys(raw, {"id", "name", "aliases", "include", "exclude", "sources", "source_categories"})
        categories = raw["source_categories"]
        sources = self._sources(raw["sources"])
        if not isinstance(categories, dict) or not set(categories) <= set(sources):
            raise WatchConfigurationError("invalid_source_categories")
        return {
            "id": self._identifier(raw["id"]),
            "name": self._text(raw["name"]),
            "aliases": self._strings(raw["aliases"]),
            "include": self._strings(raw["include"]),
            "exclude": self._strings(raw["exclude"]),
            "sources": sources,
            "source_categories": {key: self._strings(value) for key, value in categories.items()},
        }

    def _profile(self, raw: Any) -> dict[str, Any]:
        self._keys(raw, {"id", "reader_id", "name", "scope_text", "domains", "filters"})
        if not isinstance(raw["domains"], list):
            raise WatchConfigurationError("invalid_domains")
        domains = []
        for item in raw["domains"]:
            self._keys(item, {"id", "revision"})
            if type(item["revision"]) is not int or not 1 <= item["revision"] < 2**63:
                raise WatchConfigurationError("invalid_domain_revision")
            domains.append({"id": self._identifier(item["id"]), "revision": item["revision"]})
        if len({d["id"] for d in domains}) != len(domains):
            raise WatchConfigurationError("duplicate_domain")
        filters = raw["filters"]
        self._keys(filters, {"include", "exclude", "languages", "sources", "free_only", "allow_preprints"})
        for name in ("free_only", "allow_preprints"):
            if type(filters[name]) is not bool:
                raise WatchConfigurationError("invalid_boolean", name)
        return {
            "id": self._identifier(raw["id"]),
            "reader_id": self._identifier(raw["reader_id"]),
            "name": self._text(raw["name"]),
            "scope_text": self._text(raw["scope_text"]),
            "domains": sorted(domains, key=lambda item: item["id"]),
            "filters": {
                "include": self._strings(filters["include"]),
                "exclude": self._strings(filters["exclude"]),
                "languages": self._strings(filters["languages"]),
                "sources": self._sources(filters["sources"]),
                "free_only": filters["free_only"],
                "allow_preprints": filters["allow_preprints"],
            },
        }
