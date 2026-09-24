from datetime import datetime

from libs.discovery.dtos.pubmed_harvest_run_result import PubmedHarvestRunResult
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.ports.pubmed_harvest_store_port import PubmedHarvestStorePort
from libs.discovery.ports.pubmed_source_port import PubmedSourcePort
from libs.discovery.ports.source_http_transport_port import SourceHttpTransportPort
from libs.kernel.ports.publish_object_port import PublishObjectPort


class RunPubmedHarvestWindow:
    def __init__(
        self,
        source: PubmedSourcePort,
        transport: SourceHttpTransportPort,
        objects: PublishObjectPort,
        store: PubmedHarvestStorePort,
    ) -> None:
        self._source = source
        self._transport = transport
        self._objects = objects
        self._store = store

    @staticmethod
    def _response(response: SourceHttpResponse) -> SourceHttpResponse:
        if not isinstance(response, SourceHttpResponse):
            raise SourceFetchError("invalid_source_response")
        if response.status == 429:
            raise SourceFetchError(
                "source_rate_limited",
                retryable=True,
                retry_after_seconds=60.0,
            )
        if 500 <= response.status < 600:
            raise SourceFetchError(
                "source_server_error",
                retryable=True,
                retry_after_seconds=30.0,
            )
        if response.capture_error is not None:
            retryable = response.capture_error in {
                "source_timeout",
                "response_incomplete",
                "source_connection_error",
            }
            raise SourceFetchError(
                response.capture_error,
                retryable=retryable,
                retry_after_seconds=30.0 if retryable else None,
            )
        if response.status != 200:
            raise SourceFetchError("unexpected_http_status")
        if not isinstance(response.body, bytes) or not response.body:
            raise SourceFetchError("empty_source_response")
        return response

    def __call__(
        self,
        query: SourceQueryInput,
        *,
        started_at: datetime,
        max_batches: int = 10,
    ) -> PubmedHarvestRunResult:
        if type(max_batches) is not int or not 1 <= max_batches <= 100:
            raise HarvestError("invalid_pubmed_batch_limit")
        plan = self._source.compile(query)
        progress = self._store.ensure(plan, started_at)
        search_fetches = 0
        bibliography_fetches = 0

        while bibliography_fetches < max_batches:
            progress = self._store.read(plan)
            if progress.state in {"succeeded", "verified_empty", "unavailable"}:
                return PubmedHarvestRunResult(
                    progress,
                    "complete" if progress.state != "unavailable" else "unavailable",
                    search_fetches,
                    bibliography_fetches,
                )

            if progress.page_start is None:
                request = self._source.page(plan, progress.next_start)
                response = self._response(self._transport.get(request))
                page = self._source.parse_search(
                    request,
                    response.body,
                    http_status=response.status,
                )
                ref = self._objects(
                    response.body,
                    "raw",
                    "application/json; charset=utf-8",
                    "source-response",
                )
                progress = self._store.save_search(
                    plan,
                    page,
                    ref.object_id,
                    response.received_at,
                )
                search_fetches += 1
                if progress.state in {"succeeded", "verified_empty"}:
                    continue

            pending = self._store.next_batch(
                plan,
                maximum_batch_size=100,
            )
            if pending is None:
                continue
            request = self._source.bibliography_request(pending.pmids)
            response = self._response(self._transport.get(request))
            batch = self._source.parse_bibliography(
                request,
                response.body,
                http_status=response.status,
            )
            ref = self._objects(
                response.body,
                "raw",
                "application/xml; charset=utf-8",
                "source-response",
            )
            progress = self._store.save_bibliography(
                plan,
                pending,
                batch,
                ref.object_id,
                response.received_at,
            )
            bibliography_fetches += 1

        progress = self._store.read(plan)
        return PubmedHarvestRunResult(
            progress,
            "complete"
            if progress.state in {"succeeded", "verified_empty"}
            else "page_budget",
            search_fetches,
            bibliography_fetches,
        )
