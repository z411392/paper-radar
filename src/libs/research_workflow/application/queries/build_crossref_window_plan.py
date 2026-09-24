import re

from libs.discovery.dtos.crossref_page import CrossrefWindowInput, CrossrefWindowPlan
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError


class BuildCrossrefWindowPlan:
    """Map one exact published domain binding into the Crossref list protocol."""

    def __init__(
        self,
        source: CrossrefPageSourcePort,
        *,
        contact_email: str,
    ) -> None:
        self._source = source
        self._contact_email = contact_email

    @staticmethod
    def _term(value: object) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value.encode("utf-8")) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise HarvestWorkflowError("invalid_crossref_scope")
        return value

    def __call__(
        self,
        query: SourceQueryInput,
        *,
        binding_key: str,
    ) -> CrossrefWindowPlan:
        if not isinstance(query, SourceQueryInput) or query.source_id != "crossref":
            raise HarvestWorkflowError("invalid_crossref_query_input")
        if query.time_basis != "indexDate":
            raise HarvestWorkflowError("invalid_crossref_time_basis")
        if (
            type(query.page_size) is not int
            or not 1 <= query.page_size <= 1000
            or re.fullmatch(r"[0-9a-f]{64}", query.profile_fingerprint) is None
        ):
            raise HarvestWorkflowError("invalid_crossref_query_input")

        terms = tuple(
            sorted(
                {
                    self._term(value)
                    for value in (
                        *query.domain.aliases,
                        *query.domain.include,
                        *query.profile_include,
                    )
                }
            )
        )
        if not terms:
            raise HarvestWorkflowError("invalid_crossref_scope")
        config_version = (
            f"p{query.profile_revision}-d{query.domain.revision}-"
            f"{query.profile_fingerprint[:24]}"
        )
        definition = CrossrefWindowInput(
            binding_key=binding_key,
            scope_query=" ".join(terms),
            from_index=query.window_start,
            until_index=query.window_end,
            contact_email=self._contact_email,
            config_version=config_version,
            rows=query.page_size,
        )
        try:
            return self._source.compile(definition)
        except CrossrefProtocolError as exc:
            raise HarvestWorkflowError(exc.code) from exc
