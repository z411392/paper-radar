from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.research_workflow.domain.services.harvest_query_rules import HarvestQueryRules
from libs.research_workflow.dtos.harvest_query_request import HarvestQueryRequest
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.watch_profiles.ports.read_domain_definition_port import ReadDomainDefinitionPort
from libs.watch_profiles.ports.read_watch_profile_port import ReadWatchProfilePort


class BuildHarvestQueryInput:
    """Map exact published configuration into the discovery-owned query DTO."""

    def __init__(
        self,
        profiles: ReadWatchProfilePort,
        domains: ReadDomainDefinitionPort,
    ) -> None:
        self._profiles = profiles
        self._domains = domains

    def __call__(self, request: HarvestQueryRequest) -> SourceQueryInput:
        if not isinstance(request, HarvestQueryRequest):
            raise HarvestWorkflowError("invalid_query_request")
        if request.source_id != "arxiv":
            raise HarvestWorkflowError("unsupported_source")
        profile = self._profiles(request.profile_id)
        if (
            profile.profile_id != request.profile_id
            or profile.lifecycle != "active"
            or profile.revision != profile.current_revision
        ):
            raise HarvestWorkflowError("profile_not_current")
        revision = HarvestQueryRules.profile_domain_revision(profile, request.domain_id)
        filters = HarvestQueryRules.filters(profile)
        if request.source_id not in filters["sources"]:
            raise HarvestWorkflowError("source_not_selected")
        domain = self._domains(request.domain_id, revision)
        HarvestQueryRules.domain(domain, request.domain_id, revision)
        if request.source_id not in domain.sources:
            raise HarvestWorkflowError("source_not_selected")
        categories = dict(domain.source_categories).get(request.source_id, ())
        return SourceQueryInput(
            request.source_id,
            profile.profile_id,
            profile.revision,
            profile.fingerprint,
            DomainQuerySnapshot(
                domain.domain_id,
                domain.revision,
                domain.sources,
                categories,
                domain.aliases,
                domain.include,
                domain.exclude,
            ),
            request.window_start,
            request.window_end,
            tuple(filters["sources"]),
            tuple(filters["include"]),
            tuple(filters["exclude"]),
            tuple(filters["languages"]),
            filters["free_only"],
            filters["allow_preprints"],
            profile.scope_text,
            request.deferred_mode,
            request.time_basis,
            request.page_size,
        )
