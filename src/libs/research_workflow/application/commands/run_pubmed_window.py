from libs.discovery.domain.services.prepare_pubmed_bibliography_observations import (
    PreparePubmedBibliographyObservations,
)
from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.pubmed_window_error import PubmedWindowError
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.ports.pubmed_source_port import PubmedSourcePort
from libs.discovery.ports.pubmed_window_store_port import PubmedWindowStorePort
from libs.discovery.ports.source_page_fetcher_port import SourcePageFetcherPort
from libs.kernel.ports.publish_object_port import PublishObjectPort
from libs.research_workflow.dtos.pubmed_window_run_result import PubmedWindowRunResult
from libs.research_workflow.ports.workflow_clock_port import WorkflowClockPort
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.ports.resolve_paper_identity_port import ResolvePaperIdentityPort


class RunPubmedWindow:
    def __init__(
        self,
        *,
        source: PubmedSourcePort,
        fetcher: SourcePageFetcherPort,
        objects: PublishObjectPort,
        store: PubmedWindowStorePort,
        catalog: ResolvePaperIdentityPort,
        clock: WorkflowClockPort,
    ) -> None:
        self._source = source
        self._fetcher = fetcher
        self._objects = objects
        self._store = store
        self._catalog = catalog
        self._clock = clock
        self._prepare = PreparePubmedBibliographyObservations()

    def _fetch(self, request: SourcePageRequest) -> SourceFetchResult:
        result = self._fetcher.fetch(request)
        if result.failure_code is not None:
            raise SourceFetchError(
                result.failure_code,
                retryable=result.retryable,
                retry_after_seconds=result.retry_after_seconds,
            )
        response = result.response
        if (
            response is None
            or result.response_sha256 is None
            or response.capture_error is not None
            or response.status != 200
        ):
            raise PubmedWindowError("invalid_pubmed_fetch_result")
        ref = self._objects(
            response.body,
            "raw",
            "application/octet-stream",
            "source-response",
        )
        if ref.object_id != "raw:" + result.response_sha256 or ref.state != "available":
            raise PubmedWindowError("pubmed_raw_object_mismatch")
        return result

    def __call__(
        self,
        business_key: str,
        query: SourceQueryInput,
        *,
        max_pages: int = 10,
    ) -> PubmedWindowRunResult:
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise PubmedWindowError("invalid_pubmed_page_budget")
        plan = self._source.compile(query)
        progress = self._store.ensure(
            business_key,
            plan,
            created_at=self._clock.now(),
        )
        fetched = 0
        processed = 0
        while processed < max_pages:
            if progress.state in {"succeeded", "verified_empty"}:
                return PubmedWindowRunResult(
                    progress.state,
                    "complete",
                    fetched,
                    processed,
                    progress.next_start,
                    progress.total_results,
                )

            pending = progress.pending_page
            if pending is None:
                search_request = self._source.page(plan, progress.next_start)
                search_result = self._fetch(search_request)
                fetched += 1
                assert search_result.response is not None
                assert search_result.response_sha256 is not None
                search_page = self._source.parse_search(
                    search_request,
                    search_result.response.body,
                    http_status=search_result.response.status,
                )
                progress = self._store.record_search_page(
                    business_key,
                    search_page,
                    raw_object_id="raw:" + search_result.response_sha256,
                    observed_at=search_result.response.received_at,
                )
                if progress.state == "verified_empty":
                    return PubmedWindowRunResult(
                        progress.state,
                        "complete",
                        fetched,
                        processed,
                        progress.next_start,
                        progress.total_results,
                    )
                pending = progress.pending_page
                if pending is None:
                    raise PubmedWindowError("pubmed_pending_page_missing")

            bibliography_request = self._source.bibliography_request(pending.pmids)
            bibliography_result = self._fetch(bibliography_request)
            fetched += 1
            assert bibliography_result.response is not None
            assert bibliography_result.response_sha256 is not None
            batch = self._source.parse_bibliography(
                bibliography_request,
                bibliography_result.response.body,
                http_status=bibliography_result.response.status,
            )
            observations = self._prepare(
                business_key=business_key,
                start_index=pending.start_index,
                batch=batch,
                raw_object_id="raw:" + bibliography_result.response_sha256,
                observed_at=bibliography_result.response.received_at,
            )
            records = {record.pmid: record for record in batch.records}
            for observation in observations:
                record = records.get(observation.pmid)
                if record is None:
                    raise PubmedWindowError("pubmed_bibliography_identity_mismatch")
                self._catalog(
                    PaperIdentityObservation(
                        observation.observation_id,
                        "pmid",
                        record.pmid,
                        record.title,
                        observation.content_fingerprint,
                        "publication",
                        f"https://pubmed.ncbi.nlm.nih.gov/{record.pmid}/",
                        "published",
                        observation.observed_at,
                        None,
                        None,
                    )
                )
            progress = self._store.commit_bibliography(
                business_key,
                observations,
            )
            processed += 1

        return PubmedWindowRunResult(
            progress.state,
            "page_budget",
            fetched,
            processed,
            progress.next_start,
            progress.total_results,
        )
