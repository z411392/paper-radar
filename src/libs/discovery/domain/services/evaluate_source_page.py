from libs.discovery.dtos.page_traversal_decision import PageTraversalDecision
from libs.discovery.dtos.source_page_observation import SourcePageObservation
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_query_error import SourceQueryError


class EvaluateSourcePage:
    """Reject ambiguous traversal. This pure computation cannot commit a checkpoint."""

    def __call__(
        self,
        request: SourcePageRequest,
        page: SourcePageObservation,
        *,
        expected_total_results: int | None = None,
    ) -> PageTraversalDecision:
        if page.status != "ok":
            raise SourceQueryError("source_page_failed")
        if (page.source_id, page.query_fingerprint) != (request.source_id, request.query_fingerprint):
            raise SourceQueryError("page_query_mismatch")
        for value in (
            request.start,
            request.max_results,
            request.maximum_window_results,
            page.start_index,
            page.total_results,
        ):
            if type(value) is not int or value < 0:
                raise SourceQueryError("invalid_page_counts")
        if not request.max_results or not request.maximum_window_results:
            raise SourceQueryError("invalid_page_counts")
        if page.start_index != request.start:
            raise SourceQueryError("page_offset_mismatch")
        if page.total_results > request.maximum_window_results:
            raise SourceQueryError("query_window_too_large")
        if expected_total_results is not None:
            if type(expected_total_results) is not int or expected_total_results < 0:
                raise SourceQueryError("invalid_page_counts")
            if page.total_results != expected_total_results:
                raise SourceQueryError("source_result_set_changed")
        if not isinstance(page.record_ids, tuple):
            raise SourceQueryError("invalid_page_identity")
        if any(not isinstance(value, str) or not value.strip() for value in page.record_ids):
            raise SourceQueryError("invalid_page_identity")
        if len(set(page.record_ids)) != len(page.record_ids):
            raise SourceQueryError("duplicate_page_identity")
        count = len(page.record_ids)
        following = request.start + count
        if count > request.max_results or following > page.total_results:
            raise SourceQueryError("invalid_page_counts")
        if count == 0:
            if request.start != page.total_results:
                raise SourceQueryError("unexpected_empty_page")
            return PageTraversalDecision(None, True, page.total_results == 0 and request.start == 0)
        if following < page.total_results:
            if count < request.max_results:
                raise SourceQueryError("incomplete_page")
            return PageTraversalDecision(following, False, False)
        return PageTraversalDecision(None, True, False)
