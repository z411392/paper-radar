"""Pure Crossref list protocol; no HTTP, persistence, merging or status updates.

The caller must publish a raw receipt before decoding its bytes. This adapter
checks byte identity but cannot establish that a supplied hash is durable.
"""

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from libs.discovery.dtos.crossref_page import (
    CrossrefDecodedItem,
    CrossrefDecodedPage,
    CrossrefPageRequest,
    CrossrefWindowInput,
    CrossrefWindowPlan,
)
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError


class CrossrefSourceAdapter:
    ENDPOINT = "https://api.crossref.org/works"
    VERSION = "crossref-rest-page-v1"
    MAX_BODY_BYTES = 8_000_000
    MAX_CURSOR_BYTES = 65_536
    MAX_WINDOW_SECONDS = 86_400  # Local v1 policy, not an upstream API limit.
    MAX_JSON_DEPTH = 64
    MAX_JSON_NODES = 200_000

    @staticmethod
    def _canonical(value: object) -> str:
        # This is a canonical JSON-value representation, not a raw byte slice.
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def _hash(cls, value: object) -> str:
        return hashlib.sha256(cls._canonical(value).encode("ascii")).hexdigest()

    @staticmethod
    def _text(value: object, code: str, maximum: int) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise CrossrefProtocolError(code)
        try:
            if len(value.encode("utf-8")) > maximum:
                raise CrossrefProtocolError(code)
        except UnicodeEncodeError:
            raise CrossrefProtocolError(code) from None
        return value

    @staticmethod
    def _instant(value: object) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise CrossrefProtocolError("invalid_crossref_window")
        try:
            if value.utcoffset() is None:
                raise ValueError
            normalized = value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise CrossrefProtocolError("invalid_crossref_window") from None
        if normalized.microsecond:
            raise CrossrefProtocolError("unsupported_crossref_time_precision")
        return normalized

    def compile(self, definition: CrossrefWindowInput) -> CrossrefWindowPlan:
        if not isinstance(definition, CrossrefWindowInput):
            raise CrossrefProtocolError("invalid_crossref_definition")
        binding = self._text(definition.binding_key, "invalid_crossref_binding", 512)
        scope = self._text(definition.scope_query, "invalid_crossref_scope", 4096)
        version = self._text(definition.config_version, "invalid_crossref_config_version", 128)
        email = self._text(definition.contact_email, "invalid_crossref_contact", 254)
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is None:
            raise CrossrefProtocolError("invalid_crossref_contact")
        rows = definition.rows
        if type(rows) is not int or not 1 <= rows <= 1000:
            raise CrossrefProtocolError("invalid_crossref_rows")
        start = self._instant(definition.from_index)
        end = self._instant(definition.until_index)
        if not 0 < (end - start).total_seconds() <= self.MAX_WINDOW_SECONDS:
            raise CrossrefProtocolError("invalid_crossref_window")
        normalized = CrossrefWindowInput(binding, scope, start, end, email, version, rows)
        start_text = start.isoformat(timespec="seconds").removesuffix("+00:00")
        end_text = end.isoformat(timespec="seconds").removesuffix("+00:00")
        parameters = tuple(sorted((
            ("filter", f"from-index-date:{start_text},until-index-date:{end_text}"),
            ("query.bibliographic", scope),
            ("rows", str(rows)),
            ("mailto", email),
        )))
        semantic = {
            "protocol_version": self.VERSION,
            "endpoint": self.ENDPOINT,
            "binding_key": binding,
            "config_version": version,
            "parameters": [(key, value) for key, value in parameters if key != "mailto"],
        }
        return CrossrefWindowPlan(
            normalized, parameters, self._hash(semantic), self._hash(parameters)
        )

    def _check_plan(self, plan: CrossrefWindowPlan) -> None:
        if not isinstance(plan, CrossrefWindowPlan):
            raise CrossrefProtocolError("invalid_crossref_plan")
        try:
            expected = self.compile(plan.definition)
        except CrossrefProtocolError:
            raise CrossrefProtocolError("invalid_crossref_plan") from None
        if expected != plan:
            raise CrossrefProtocolError("invalid_crossref_plan")

    def page(self, plan: CrossrefWindowPlan, cursor: str = "*") -> CrossrefPageRequest:
        self._check_plan(plan)
        cursor = self._text(cursor, "invalid_crossref_cursor", self.MAX_CURSOR_BYTES)
        parameters = (*plan.parameters, ("cursor", cursor))
        url = self.ENDPOINT + "?" + urlencode(parameters)
        fingerprint = self._hash({
            "protocol_version": self.VERSION,
            "query_fingerprint": plan.query_fingerprint,
            "parameters_fingerprint": plan.parameters_fingerprint,
            "method": "GET",
            "url": url,
        })
        return CrossrefPageRequest(
            plan.query_fingerprint, plan.parameters_fingerprint, cursor, url, fingerprint
        )

    @classmethod
    def _check_depth(cls, text: str) -> None:
        depth = 0
        quoted = escaped = False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > cls.MAX_JSON_DEPTH:
                    raise ValueError("depth")
            elif char in "]}":
                depth -= 1

    @staticmethod
    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    @staticmethod
    def _nonfinite(value: str) -> None:
        raise ValueError("nonfinite")

    @classmethod
    def _load(cls, body: bytes) -> dict:
        text = body.decode("utf-8")
        cls._check_depth(text)
        value = json.loads(text, object_pairs_hook=cls._pairs, parse_constant=cls._nonfinite)
        pending = [value]
        nodes = 0
        while pending:
            current = pending.pop()
            nodes += 1
            if nodes > cls.MAX_JSON_NODES:
                raise ValueError("node limit")
            if isinstance(current, dict):
                pending.extend(current.values())
            elif isinstance(current, list):
                pending.extend(current)
            elif isinstance(current, float) and not math.isfinite(current):
                raise ValueError("nonfinite")
        if not isinstance(value, dict):
            raise ValueError("envelope")
        return value

    @classmethod
    def _item(cls, ordinal: int, value: object) -> CrossrefDecodedItem:
        canonical = cls._canonical(value)
        raw_doi = value.get("DOI") if isinstance(value, dict) else None
        error = None
        try:
            # Syntactic field check only. Owner normalization/resolution is a later stage.
            cls._text(raw_doi, "crossref_item_doi_field_invalid", 2048)
        except CrossrefProtocolError:
            error = "crossref_item_doi_field_invalid"
        return CrossrefDecodedItem(
            ordinal,
            "quarantined" if error else "decoded",
            raw_doi if isinstance(raw_doi, str) else None,
            canonical,
            hashlib.sha256(canonical.encode("ascii")).hexdigest(),
            error,
        )

    def decode(
        self,
        plan: CrossrefWindowPlan,
        request: CrossrefPageRequest,
        body: bytes,
        *,
        expected_sha256: str,
        http_status: int = 200,
    ) -> CrossrefDecodedPage:
        self._check_plan(plan)
        if not isinstance(request, CrossrefPageRequest):
            raise CrossrefProtocolError("invalid_crossref_request")
        try:
            valid_request = self.page(plan, request.cursor)
        except CrossrefProtocolError:
            raise CrossrefProtocolError("invalid_crossref_request") from None
        if request != valid_request:
            raise CrossrefProtocolError("invalid_crossref_request")
        if not isinstance(body, bytes):
            raise CrossrefProtocolError("invalid_crossref_body")
        digest = hashlib.sha256(body).hexdigest()
        if len(body) > self.MAX_BODY_BYTES:
            raise CrossrefProtocolError("crossref_response_too_large", digest)
        if (
            not isinstance(expected_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or digest != expected_sha256
        ):
            raise CrossrefProtocolError("crossref_response_hash_mismatch", digest)
        if type(http_status) is not int or http_status != 200:
            raise CrossrefProtocolError("crossref_http_not_successful", digest)
        try:
            envelope = self._load(body)
        except (ValueError, UnicodeError, RecursionError, OverflowError):
            raise CrossrefProtocolError("invalid_crossref_json", digest) from None
        if envelope.get("status") != "ok" or envelope.get("message-type") != "work-list":
            raise CrossrefProtocolError("invalid_crossref_envelope", digest)
        message = envelope.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("items"), list):
            raise CrossrefProtocolError("invalid_crossref_envelope", digest)
        values = message["items"]
        total = message.get("total-results")
        if (
            len(values) > plan.definition.rows
            or (total is not None and (type(total) is not int or not 0 <= total < 2**63))
        ):
            raise CrossrefProtocolError("invalid_crossref_envelope", digest)
        next_cursor = message.get("next-cursor")
        if next_cursor is not None:
            try:
                self._text(next_cursor, "invalid_crossref_cursor", self.MAX_CURSOR_BYTES)
            except CrossrefProtocolError:
                raise CrossrefProtocolError("invalid_crossref_cursor", digest) from None
        end_hint = len(values) < plan.definition.rows
        if not end_hint:
            if next_cursor is None:
                raise CrossrefProtocolError("crossref_next_cursor_missing", digest)
            if next_cursor == request.cursor or next_cursor == "*":
                raise CrossrefProtocolError("crossref_cursor_no_progress", digest)
        return CrossrefDecodedPage(
            self.VERSION,
            plan.query_fingerprint,
            plan.parameters_fingerprint,
            request.request_fingerprint,
            digest,
            request.cursor,
            next_cursor,
            total,
            tuple(self._item(ordinal, value) for ordinal, value in enumerate(values)),
            end_hint,
        )
