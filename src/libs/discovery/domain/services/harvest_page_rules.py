import hashlib
import json
import re
from datetime import datetime
from typing import Any

from libs.discovery.domain.services.evaluate_source_page import EvaluateSourcePage
from libs.discovery.domain.services.prepare_harvest_capture import PrepareHarvestCapture
from libs.discovery.dtos.harvest_page_result import HarvestPageResult
from libs.discovery.dtos.harvest_processing_snapshot import HarvestProcessingSnapshot
from libs.discovery.dtos.parsed_arxiv_page import ParsedArxivPage
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.exceptions.source_query_error import SourceQueryError


class HarvestPageRules:
    VERSION = "harvest-page-v1"
    MAX_ENVELOPE_BYTES = 2_000_000

    @staticmethod
    def encode(value: object) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    @classmethod
    def decode(cls, text: str) -> dict[str, Any]:
        try:
            if not isinstance(text, str) or len(text.encode("utf-8")) > cls.MAX_ENVELOPE_BYTES:
                raise HarvestError("invalid_processing_metadata")
            value = json.loads(text)
            if not isinstance(value, dict) or cls.encode(value) != text:
                raise HarvestError("invalid_processing_metadata")
            return value
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise HarvestError("invalid_processing_metadata") from None

    @classmethod
    def key(cls, parser_version: str) -> str:
        if (
            not isinstance(parser_version, str)
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", parser_version) is None
        ):
            raise HarvestError("invalid_parser_version")
        return cls.VERSION + "/" + parser_version

    @staticmethod
    def checkpoint(value: int) -> None:
        if type(value) is not int or not 0 <= value < 2**63 - 1:
            raise HarvestError("invalid_checkpoint_version")

    @classmethod
    def receipt(cls, value: object, attempt_id: str, unit_id: str, parser_version: str) -> HarvestPageResult:
        try:
            if not isinstance(value, dict) or not isinstance(value.get("observation_ids"), list):
                raise HarvestError("invalid_processing_receipt")
            result = HarvestPageResult(**{**value, "observation_ids": tuple(value["observation_ids"])})
            cls.checkpoint(result.expected_checkpoint_version)
            cls.checkpoint(result.checkpoint_version)
            if (
                result.attempt_id != attempt_id
                or result.unit_id != unit_id
                or result.processor_version != cls.VERSION
                or result.parser_version != parser_version
                or type(result.next_start) is not int
                or not 0 <= result.next_start <= 30000
                or (
                    result.total_results is not None
                    and (
                        type(result.total_results) is not int
                        or not 0 <= result.total_results <= 30000
                        or result.next_start > result.total_results
                    )
                )
                or len(result.observation_ids) > 2000
                or any(
                    not isinstance(i, str) or re.fullmatch(r"observation:[0-9a-f]{64}", i) is None
                    for i in result.observation_ids
                )
                or len(set(result.observation_ids)) != len(result.observation_ids)
            ):
                raise HarvestError("invalid_processing_receipt")
            PrepareHarvestCapture.code(result.error_code)
            if result.error_code is None:
                if (
                    result.state not in {"succeeded", "verified_empty", "partial"}
                    or result.checkpoint_version != result.expected_checkpoint_version + 1
                ):
                    raise HarvestError("invalid_processing_receipt")
            elif (
                result.state not in {"failed", "partial"}
                or result.observation_ids
                or result.checkpoint_version != result.expected_checkpoint_version
            ):
                raise HarvestError("invalid_processing_receipt")
            if result.error_code is None:
                total = result.total_results
                if total is None:
                    raise HarvestError("invalid_processing_receipt")
                if result.state == "verified_empty":
                    if (
                        total != 0
                        or result.next_start != 0
                        or result.observation_ids
                        or result.expected_checkpoint_version != 0
                    ):
                        raise HarvestError("invalid_processing_receipt")
                elif (
                    not result.observation_ids
                    or total <= 0
                    or (result.state == "succeeded" and result.next_start != total)
                    or (result.state == "partial" and not 0 < result.next_start < total)
                ):
                    raise HarvestError("invalid_processing_receipt")
            elif (
                result.state == "failed"
                and (
                    result.expected_checkpoint_version != 0
                    or result.next_start != 0
                    or result.total_results is not None
                )
            ) or (
                result.state == "partial"
                and (result.expected_checkpoint_version == 0 or result.next_start == 0)
            ):
                raise HarvestError("invalid_processing_receipt")
            if PrepareHarvestCapture.time(datetime.fromisoformat(result.processed_at)) != result.processed_at:
                raise HarvestError("invalid_processing_receipt")
            return result
        except (TypeError, ValueError, KeyError, OverflowError):
            raise HarvestError("invalid_processing_receipt") from None

    @classmethod
    def ready(cls, snapshot: HarvestProcessingSnapshot, expected: int, processed_at: datetime) -> None:
        cls.checkpoint(expected)
        now = PrepareHarvestCapture.time(processed_at)
        attempt = snapshot.attempt
        if attempt.capture_json is None or attempt.finished_at is None:
            raise HarvestError("capture_not_ready")
        if now < attempt.finished_at:
            raise HarvestError("invalid_harvest_time")
        if snapshot.checkpoint_version != expected:
            raise HarvestError("checkpoint_conflict")
        if snapshot.unit_state in {"succeeded", "verified_empty", "unavailable"}:
            raise HarvestError("unit_not_open")
        if attempt.request.start != snapshot.next_start:
            raise HarvestError("checkpoint_offset_conflict")

    @classmethod
    def result(
        cls,
        snapshot: HarvestProcessingSnapshot,
        page: ParsedArxivPage | None,
        error_code: str | None,
        processed_at: datetime,
    ) -> HarvestPageResult:
        cls.ready(snapshot, snapshot.checkpoint_version, processed_at)
        PrepareHarvestCapture.code(error_code)
        attempt = snapshot.attempt
        next_start, total = snapshot.next_start, snapshot.total_results
        ids: tuple[str, ...] = ()
        if page is None:
            if error_code is None:
                raise HarvestError("missing_processing_result")
            state = "partial" if snapshot.checkpoint_version else "failed"
            version = snapshot.checkpoint_version
        else:
            if error_code is not None or not isinstance(page, ParsedArxivPage):
                raise HarvestError("invalid_processing_result")
            capture = cls.decode(attempt.capture_json or "{}")
            if (
                page.parser_version != snapshot.parser_version
                or page.request_fingerprint != attempt.request.request_fingerprint
                or page.response_sha256 != capture.get("response_sha256")
                or not isinstance(page.raw_body, bytes)
                or hashlib.sha256(page.raw_body).hexdigest() != page.response_sha256
                or len(page.raw_body) != capture.get("byte_size")
                or tuple(r.source_record_id for r in page.records) != page.observation.record_ids
            ):
                raise HarvestError("parsed_capture_mismatch")
            try:
                decision = EvaluateSourcePage()(
                    attempt.request, page.observation, expected_total_results=total
                )
            except SourceQueryError as exc:
                raise HarvestError(exc.code) from None
            if set(page.observation.record_ids) & set(snapshot.record_ids):
                raise HarvestError("duplicate_traversal_identity")
            for record in page.records:
                PrepareHarvestCapture.time(record.updated_at)
                PrepareHarvestCapture.time(record.published_at)
            ids = tuple(
                "observation:"
                + hashlib.sha256(
                    cls.encode(
                        [
                            attempt.unit_id,
                            attempt.request.source_id,
                            record.source_record_id,
                            capture["raw_object_id"],
                            page.parser_version,
                        ]
                    ).encode("utf-8")
                ).hexdigest()
                for record in page.records
            )
            next_start = attempt.request.start + len(page.records)
            total = page.observation.total_results
            state = (
                "verified_empty"
                if decision.verified_empty
                else "succeeded"
                if decision.is_last
                else "partial"
            )
            version = snapshot.checkpoint_version + 1
        cls.checkpoint(version)
        return HarvestPageResult(
            attempt.attempt_id,
            attempt.unit_id,
            cls.VERSION,
            snapshot.parser_version,
            snapshot.checkpoint_version,
            version,
            next_start,
            total,
            state,
            ids,
            error_code,
            PrepareHarvestCapture.time(processed_at),
        )
