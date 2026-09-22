import hashlib
from datetime import datetime

from libs.discovery.domain.services.harvest_page_rules import HarvestPageRules
from libs.discovery.domain.services.prepare_harvest_capture import PrepareHarvestCapture
from libs.discovery.dtos.harvest_page_result import HarvestPageResult
from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.exceptions.source_parse_error import SourceParseError
from libs.discovery.ports.harvest_processing_store_port import HarvestProcessingStorePort
from libs.discovery.ports.parse_source_page_port import ParseSourcePagePort
from libs.discovery.ports.read_harvest_attempt_port import ReadHarvestAttemptPort
from libs.kernel.ports.read_object_port import ReadObjectPort


class ProcessHarvestPage:
    def __init__(
        self, attempts: ReadHarvestAttemptPort, objects: ReadObjectPort,
        parser: ParseSourcePagePort, store: HarvestProcessingStorePort, parser_version: str,
    ) -> None:
        HarvestPageRules.key(parser_version)
        self._attempts, self._objects, self._parser = attempts, objects, parser
        self._store, self._parser_version = store, parser_version

    def __call__(
        self, attempt_id: str, expected_checkpoint_version: int, processed_at: datetime
    ) -> HarvestPageResult:
        HarvestPageRules.checkpoint(expected_checkpoint_version)
        PrepareHarvestCapture.time(processed_at)
        attempt = self._attempts(attempt_id)
        snapshot = self._store.snapshot(attempt, self._parser_version)
        if snapshot.previous_result is not None:
            if snapshot.previous_result.expected_checkpoint_version != expected_checkpoint_version:
                raise HarvestError("checkpoint_conflict")
            return snapshot.previous_result
        HarvestPageRules.ready(snapshot, expected_checkpoint_version, processed_at)
        capture = HarvestPageRules.decode(attempt.capture_json or "{}")
        # Reconstruct the captured response through the same pure S1 validator.
        # Reading missing/corrupt raw must fail before any processing receipt is written.
        try:
            body = None
            response = None
            if capture["raw_object_id"] is not None:
                raw_id, digest = capture["raw_object_id"], capture["response_sha256"]
                if not isinstance(raw_id, str) or raw_id != "raw:" + str(digest):
                    raise HarvestError("invalid_capture_metadata")
                body = self._objects(raw_id)
                if not isinstance(body, bytes) or len(body) != capture["byte_size"] or hashlib.sha256(body).hexdigest() != digest:
                    raise HarvestError("capture_hash_mismatch")
                response = SourceHttpResponse(
                    capture["status"], body, tuple(tuple(pair) for pair in capture["headers"]),
                    datetime.fromisoformat(capture["received_at"]), capture["capture_error"],
                )
            result = SourceFetchResult(
                capture["request_fingerprint"], response, capture["response_sha256"],
                capture["failure_code"], capture["retryable"], capture["retry_after_seconds"],
            )
            prepared = PrepareHarvestCapture()(attempt, result, datetime.fromisoformat(attempt.finished_at or ""))
            if prepared.metadata_json != attempt.capture_json:
                raise HarvestError("invalid_capture_metadata")
        except (KeyError, TypeError, ValueError, OverflowError):
            raise HarvestError("invalid_capture_metadata") from None
        if result.failure_code is not None:
            return self._store.commit(snapshot, None, result.failure_code, processed_at)
        if body is None:
            raise HarvestError("invalid_capture_metadata")
        try:
            page = self._parser(attempt.request, body, http_status=200)
            HarvestPageRules.result(snapshot, page, None, processed_at)
        except SourceParseError as exc:
            return self._store.commit(snapshot, None, exc.code, processed_at)
        except HarvestError as exc:
            # Only source traversal failures are durable outcomes. Contract violations
            # or storage problems need repair, not a cached failure masking the problem.
            if exc.code not in {"source_result_set_changed", "duplicate_traversal_identity"}:
                raise
            return self._store.commit(snapshot, None, exc.code, processed_at)
        return self._store.commit(snapshot, page, None, processed_at)
