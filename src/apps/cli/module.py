from pathlib import Path

from injector import Binder, InstanceProvider, Module, singleton

from libs.delivery.adapters.driven.json_recipient_resolver_adapter import (
    JsonRecipientResolverAdapter,
)
from libs.delivery.adapters.driven.kernel_digest_artifact_adapter import KernelDigestArtifactAdapter
from libs.delivery.adapters.driven.smtp_mail_sender_adapter import SmtpMailSenderAdapter
from libs.delivery.adapters.driven.sqlite_delivery_health_adapter import SqliteDeliveryHealthAdapter
from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import SqliteDeliveryStoreAdapter
from libs.delivery.adapters.driven.sqlite_delivery_subscription_store_adapter import (
    SqliteDeliverySubscriptionStoreAdapter,
)
from libs.delivery.adapters.driven.sqlite_digest_delivery_context_adapter import (
    SqliteDigestDeliveryContextAdapter,
)
from libs.delivery.adapters.driven.sqlite_prior_recipient_history_adapter import (
    SqlitePriorRecipientHistoryAdapter,
)
from libs.delivery.application.commands.configure_delivery_subscription import (
    ConfigureDeliverySubscription,
)
from libs.delivery.application.commands.dispatch_digest import DispatchDigest
from libs.delivery.application.commands.prepare_scheduled_digest import PrepareScheduledDigest
from libs.delivery.application.commands.queue_digest import QueueDigest
from libs.delivery.application.queries.read_delivery_subscription import (
    ReadDeliverySubscription,
)
from libs.delivery.exceptions.mail_configuration_error import MailConfigurationError
from libs.delivery.ports.configure_delivery_subscription_port import (
    ConfigureDeliverySubscriptionPort,
)
from libs.delivery.ports.dispatch_digest_port import DispatchDigestPort
from libs.delivery.ports.mail_sender_port import MailSenderPort
from libs.delivery.ports.prepare_scheduled_digest_port import PrepareScheduledDigestPort
from libs.delivery.ports.read_delivery_subscription_port import (
    ReadDeliverySubscriptionPort,
)
from libs.delivery.ports.recipient_resolver_port import RecipientResolverPort
from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import PARSER_VERSION, ArxivAtomParserAdapter
from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.adapters.driven.arxiv_source_adapter import ArxivSourceAdapter
from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.http_client_arxiv_transport_adapter import HttpClientArxivTransportAdapter
from libs.discovery.adapters.driven.http_client_crossref_transport_adapter import (
    HttpClientCrossrefTransportAdapter,
)
from libs.discovery.adapters.driven.http_client_ncbi_transport_adapter import HttpClientNcbiTransportAdapter
from libs.discovery.adapters.driven.kernel_crossref_capture_store_adapter import (
    KernelCrossrefCaptureStoreAdapter,
)
from libs.discovery.adapters.driven.posix_arxiv_rate_limit_adapter import PosixArxivRateLimitAdapter
from libs.discovery.adapters.driven.posix_crossref_rate_gate_adapter import (
    PosixCrossrefRateGateAdapter,
)
from libs.discovery.adapters.driven.posix_ncbi_rate_limit_adapter import PosixNcbiRateLimitAdapter
from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.adapters.driven.rate_limited_source_http_transport_adapter import (
    RateLimitedSourceHttpTransportAdapter,
)
from libs.discovery.adapters.driven.sqlite_arxiv_observation_replay_adapter import (
    SqliteArxivObservationReplayAdapter,
)
from libs.discovery.adapters.driven.sqlite_claimed_crossref_attachment_adapter import (
    SqliteClaimedCrossrefAttachmentAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_capture_claim_store_adapter import (
    SqliteCrossrefCaptureClaimStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_capture_inbox_adapter import (
    SqliteCrossrefCaptureInboxAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_harvest_journal_adapter import (
    SqliteCrossrefHarvestJournalAdapter,
)
from libs.discovery.adapters.driven.sqlite_harvest_coverage_adapter import SqliteHarvestCoverageAdapter
from libs.discovery.adapters.driven.sqlite_harvest_processing_adapter import SqliteHarvestProcessingAdapter
from libs.discovery.adapters.driven.sqlite_harvest_resume_adapter import SqliteHarvestResumeAdapter
from libs.discovery.adapters.driven.sqlite_harvest_store_adapter import SqliteHarvestStoreAdapter
from libs.discovery.adapters.driven.sqlite_harvest_unit_context_adapter import (
    SqliteHarvestUnitContextAdapter,
)
from libs.discovery.adapters.driven.sqlite_pubmed_harvest_store_adapter import (
    SqlitePubmedHarvestStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_pubmed_observation_replay_adapter import (
    SqlitePubmedObservationReplayAdapter,
)
from libs.discovery.adapters.driven.sqlite_source_observation_page_adapter import (
    SqliteSourceObservationPageAdapter,
)
from libs.discovery.application.commands.attach_claimed_crossref_capture import (
    AttachClaimedCrossrefCapture,
)
from libs.discovery.application.commands.capture_claimed_crossref_response import (
    CaptureClaimedCrossrefResponse,
)
from libs.discovery.application.commands.process_harvest_page import ProcessHarvestPage
from libs.discovery.application.commands.record_harvest_capture import RecordHarvestCapture
from libs.discovery.application.commands.resolve_claimed_crossref_capture import (
    ResolveClaimedCrossrefCapture,
)
from libs.discovery.application.commands.run_claimed_crossref_harvest_window import (
    RunClaimedCrossrefHarvestWindow,
)
from libs.discovery.application.commands.run_pubmed_harvest_window import RunPubmedHarvestWindow
from libs.discovery.application.commands.start_harvest_attempt import StartHarvestAttempt
from libs.discovery.application.queries.compile_source_query import CompileSourceQuery
from libs.discovery.application.queries.fetch_source_page import FetchSourcePage
from libs.discovery.application.queries.parse_source_page import ParseSourcePage
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
from libs.discovery.application.queries.read_harvest_coverage import ReadHarvestCoverage
from libs.discovery.application.queries.read_harvest_resume import ReadHarvestResume
from libs.discovery.application.queries.replay_crossref_capture import ReplayCrossrefCapture
from libs.discovery.ports.compile_source_query_port import CompileSourceQueryPort
from libs.discovery.ports.crossref_http_transport_port import CrossrefHttpTransportPort
from libs.discovery.ports.run_pubmed_harvest_window_port import RunPubmedHarvestWindowPort
from libs.discovery.ports.source_http_transport_port import SourceHttpTransportPort
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.adapters.driven.sqlite_workspace_external_effects_adapter import (
    SqliteWorkspaceExternalEffectsAdapter,
)
from libs.kernel.adapters.driven.sqlite_workspace_info_adapter import SqliteWorkspaceInfoAdapter
from libs.kernel.application.commands.initialize_workspace import InitializeWorkspace
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.commands.set_workspace_external_effects import (
    SetWorkspaceExternalEffects,
)
from libs.kernel.application.queries.read_object import ReadObject
from libs.kernel.ports.initialize_workspace_port import InitializeWorkspacePort
from libs.kernel.ports.set_workspace_external_effects_port import (
    SetWorkspaceExternalEffectsPort,
)
from libs.kernel.ports.workspace_bootstrap_port import WorkspaceBootstrapPort
from libs.paper_explanations.adapters.driven.kernel_explanation_artifact_adapter import (
    KernelExplanationArtifactAdapter,
)
from libs.paper_explanations.adapters.driven.kernel_generation_output_adapter import (
    KernelGenerationOutputAdapter,
)
from libs.paper_explanations.adapters.driven.sqlite_current_summary_store_adapter import (
    SqliteCurrentSummaryStoreAdapter,
)
from libs.paper_explanations.adapters.driven.sqlite_digest_current_summary_adapter import (
    SqliteDigestCurrentSummaryAdapter,
)
from libs.paper_explanations.adapters.driven.sqlite_explanation_health_adapter import (
    SqliteExplanationHealthAdapter,
)
from libs.paper_explanations.adapters.driven.sqlite_generation_ledger_adapter import (
    SqliteGenerationLedgerAdapter,
)
from libs.paper_explanations.adapters.driven.sqlite_verified_explanation_store_adapter import (
    SqliteVerifiedExplanationStoreAdapter,
)
from libs.paper_explanations.application.commands.extract_tracked_paper_claims import (
    ExtractTrackedPaperClaims,
)
from libs.paper_explanations.application.commands.generate_tracked_reading_card import (
    GenerateTrackedReadingCard,
)
from libs.paper_explanations.application.commands.persist_verified_explanation import (
    PersistVerifiedExplanation,
)
from libs.paper_explanations.application.commands.publish_verified_current_summary import (
    PublishVerifiedCurrentSummary,
)
from libs.paper_explanations.application.commands.run_budgeted_generation import (
    RunBudgetedGeneration,
)
from libs.paper_explanations.application.commands.verify_tracked_explanation import (
    VerifyTrackedExplanation,
)
from libs.paper_explanations.dtos.generation_budget_policy import GenerationBudgetPolicy
from libs.paper_explanations.ports.structured_generation_port import (
    StructuredGenerationPort,
)
from libs.research_workflow.adapters.driven.python_runtime_version_adapter import PythonRuntimeVersionAdapter
from libs.research_workflow.adapters.driven.sqlite_runtime_health_adapter import SqliteRuntimeHealthAdapter
from libs.research_workflow.adapters.driven.sqlite_scheduler_input_adapter import SqliteSchedulerInputAdapter
from libs.research_workflow.adapters.driven.sqlite_source_catalog_projection_store_adapter import (
    SqliteSourceCatalogProjectionStoreAdapter,
)
from libs.research_workflow.adapters.driven.sqlite_workflow_health_adapter import (
    SqliteWorkflowHealthAdapter,
)
from libs.research_workflow.adapters.driven.sqlite_workflow_job_store_adapter import (
    SqliteWorkflowJobStoreAdapter,
)
from libs.research_workflow.adapters.driven.system_harvest_runtime_adapter import SystemHarvestRuntimeAdapter
from libs.research_workflow.adapters.driven.system_workflow_clock_adapter import SystemWorkflowClockAdapter
from libs.research_workflow.application.commands.process_evidence_explanation import (
    ProcessEvidenceExplanation,
)
from libs.research_workflow.application.commands.process_revision_notice import (
    ProcessRevisionNotice,
)
from libs.research_workflow.application.commands.process_workflow_job import ProcessWorkflowJob
from libs.research_workflow.application.commands.project_source_catalog_unit import (
    ProjectSourceCatalogUnit,
)
from libs.research_workflow.application.commands.run_harvest_slice import RunHarvestSlice
from libs.research_workflow.application.commands.run_projected_crossref_harvest_window import (
    RunProjectedCrossrefHarvestWindow,
)
from libs.research_workflow.application.commands.run_scheduler_tick import RunSchedulerTick
from libs.research_workflow.application.commands.run_worker_cycle import RunWorkerCycle
from libs.research_workflow.application.queries.build_crossref_window_plan import (
    BuildCrossrefWindowPlan,
)
from libs.research_workflow.application.queries.build_harvest_query_input import BuildHarvestQueryInput
from libs.research_workflow.application.queries.inspect_health import InspectHealth
from libs.research_workflow.application.queries.read_runtime_version import ReadRuntimeVersion
from libs.research_workflow.ports.build_crossref_window_plan_port import (
    BuildCrossrefWindowPlanPort,
)
from libs.research_workflow.ports.build_harvest_query_input_port import BuildHarvestQueryInputPort
from libs.research_workflow.ports.operational_health_port import InspectHealthPort
from libs.research_workflow.ports.process_evidence_explanation_port import (
    ProcessEvidenceExplanationPort,
)
from libs.research_workflow.ports.process_revision_notice_port import (
    ProcessRevisionNoticePort,
)
from libs.research_workflow.ports.process_workflow_job_port import ProcessWorkflowJobPort
from libs.research_workflow.ports.project_source_catalog_unit_port import (
    ProjectSourceCatalogUnitPort,
)
from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort
from libs.research_workflow.ports.run_crossref_harvest_window_port import (
    RunCrossrefHarvestWindowPort,
)
from libs.research_workflow.ports.run_harvest_slice_port import RunHarvestSlicePort
from libs.research_workflow.ports.run_scheduler_tick_port import RunSchedulerTickPort
from libs.research_workflow.ports.run_worker_cycle_port import RunWorkerCyclePort
from libs.research_workflow.ports.runtime_version_provider_port import RuntimeVersionProviderPort
from libs.research_workflow.ports.workflow_clock_port import WorkflowClockPort
from libs.research_workflow.ports.workflow_job_store_port import WorkflowJobStorePort
from libs.scholarly_catalog.adapters.driven.kernel_evidence_object_adapter import (
    KernelEvidenceObjectAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_integrity_event_source_adapter import (
    SqliteCrossrefIntegrityEventSourceAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_integrity_store_adapter import (
    SqliteCrossrefIntegrityStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_integrity_work_binding_store_adapter import (
    SqliteCrossrefIntegrityWorkBindingStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_provider_revision_store_adapter import (
    SqliteCrossrefProviderRevisionStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_relation_store_adapter import (
    SqliteCrossrefRelationStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_digest_research_event_adapter import (
    SqliteDigestResearchEventAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_evidence_snapshot_store_adapter import (
    SqliteEvidenceSnapshotStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_paper_identity_store_adapter import (
    SqlitePaperIdentityStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_research_event_store_adapter import (
    SqliteResearchEventStoreAdapter,
)
from libs.scholarly_catalog.application.commands.bind_crossref_integrity_works import (
    BindCrossrefIntegrityWorks,
)
from libs.scholarly_catalog.application.commands.prepare_abstract_evidence import (
    PrepareAbstractEvidence,
)
from libs.scholarly_catalog.application.commands.prepare_evidence_snapshot import (
    PrepareEvidenceSnapshot,
)
from libs.scholarly_catalog.application.commands.project_arxiv_observation import (
    ProjectArxivObservation,
)
from libs.scholarly_catalog.application.commands.project_crossref_pending_item import (
    ProjectCrossrefPendingItem,
)
from libs.scholarly_catalog.application.commands.project_pubmed_observation import (
    ProjectPubmedObservation,
)
from libs.scholarly_catalog.application.commands.promote_crossref_integrity_events import (
    PromoteCrossrefIntegrityEvents,
)
from libs.scholarly_catalog.application.commands.record_paper_revision import (
    RecordPaperRevision,
)
from libs.scholarly_catalog.application.commands.resolve_paper_identity import (
    ResolvePaperIdentity,
)
from libs.scholarly_catalog.application.queries.read_evidence_snapshot import ReadEvidenceSnapshot
from libs.scholarly_catalog.application.queries.read_paper_identity import ReadPaperIdentity
from libs.scholarly_catalog.domain.services.evidence_snapshot_rules import EvidenceSnapshotRules
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.watch_profiles.adapters.driven.sqlite_digest_relevance_adapter import (
    SqliteDigestRelevanceAdapter,
)
from libs.watch_profiles.adapters.driven.sqlite_relevance_assessment_store_adapter import (
    SqliteRelevanceAssessmentStoreAdapter,
)
from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter import (
    SqliteWatchProfileStoreAdapter,
)
from libs.watch_profiles.application.commands.assess_tracked_paper_relevance import (
    AssessTrackedPaperRelevance,
)
from libs.watch_profiles.application.commands.import_domain_seeds import ImportDomainSeeds
from libs.watch_profiles.application.commands.persist_relevance_assessment import (
    PersistRelevanceAssessment,
)
from libs.watch_profiles.application.commands.publish_watch_profile import PublishWatchProfile
from libs.watch_profiles.application.commands.set_watch_profile_lifecycle import SetWatchProfileLifecycle
from libs.watch_profiles.application.queries.read_domain_definition import ReadDomainDefinition
from libs.watch_profiles.application.queries.read_watch_profile import ReadWatchProfile
from libs.watch_profiles.domain.services.normalize_watch_configuration import NormalizeWatchConfiguration
from libs.watch_profiles.ports.import_domain_seeds_port import ImportDomainSeedsPort
from libs.watch_profiles.ports.publish_watch_profile_port import PublishWatchProfilePort
from libs.watch_profiles.ports.read_domain_definition_port import ReadDomainDefinitionPort
from libs.watch_profiles.ports.read_watch_profile_port import ReadWatchProfilePort
from libs.watch_profiles.ports.set_watch_profile_lifecycle_port import SetWatchProfileLifecyclePort




class CliModule(Module):
    def __init__(
        self,
        workspace: str | None = None,
        *,
        with_profiles: bool = False,
        with_discovery: bool = False,
        with_runtime: bool = False,
    ) -> None:
        self._workspace = workspace
        self._with_profiles = with_profiles
        self._with_discovery = with_discovery
        self._with_runtime = with_runtime

    def configure(self, binder: Binder) -> None:
        binder.bind(RuntimeVersionProviderPort, to=PythonRuntimeVersionAdapter, scope=singleton)
        binder.bind(ReadRuntimeVersionPort, to=ReadRuntimeVersion, scope=singleton)
        if self._workspace is not None:
            bootstrap = SqliteWorkspaceBootstrapAdapter(
                Path(self._workspace),
                load_workspace_migrations(
                    with_profiles=self._with_profiles,
                    with_discovery=self._with_discovery,
                    with_runtime=self._with_runtime,
                ),
            )
            binder.bind(WorkspaceBootstrapPort, to=InstanceProvider(bootstrap), scope=singleton)
            command = InitializeWorkspace(bootstrap)
            binder.bind(InitializeWorkspacePort, to=InstanceProvider(command), scope=singleton)




class DeliverySubscriptionCliModule(Module):
    """Configure local delivery subscriptions without bootstrapping or I/O."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace

    def configure(self, binder: Binder) -> None:
        connection = SqliteSchemaConnectionFactory(
            Path(self._workspace),
            load_workspace_migrations(with_runtime=True),
            minimum_version=7,
        )
        store = SqliteDeliverySubscriptionStoreAdapter(connection.connect)
        binder.bind(
            ConfigureDeliverySubscriptionPort,
            to=InstanceProvider(ConfigureDeliverySubscription(store)),
        )
        binder.bind(
            ReadDeliverySubscriptionPort,
            to=InstanceProvider(ReadDeliverySubscription(store)),
        )


class WorkspaceEffectsCliModule(Module):
    """Explicitly mutate the workspace master I/O gate; never upgrade or start work."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace

    def configure(self, binder: Binder) -> None:
        connection = SqliteSchemaConnectionFactory(
            Path(self._workspace),
            load_workspace_migrations(with_runtime=True),
            minimum_version=24,
        )
        adapter = SqliteWorkspaceExternalEffectsAdapter(connection.connect)
        command = SetWorkspaceExternalEffects(adapter)
        binder.bind(
            SetWorkspaceExternalEffectsPort,
            to=InstanceProvider(command),
        )

class OperationalHealthCliModule(Module):
    """Assemble read-only operational evidence without enabling external effects."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace

    def configure(self, binder: Binder) -> None:
        connection = SqliteSchemaConnectionFactory(
            Path(self._workspace),
            load_workspace_migrations(with_runtime=True),
            minimum_version=25,
        )
        clock = SystemWorkflowClockAdapter()
        scheduler_inputs = SqliteSchedulerInputAdapter(connection.connect)
        query = InspectHealth(
            coverage=ReadHarvestCoverage(
                SqliteHarvestCoverageAdapter(connection.connect)
            ),
            workflow=SqliteWorkflowHealthAdapter(connection.connect),
            scheduler=scheduler_inputs,
            explanation=SqliteExplanationHealthAdapter(connection.connect),
            delivery=SqliteDeliveryHealthAdapter(connection.connect),
            runtime=SqliteRuntimeHealthAdapter(connection.connect),
            clock=clock.now,
        )
        binder.bind(InspectHealthPort, to=InstanceProvider(query))


class WatchProfileCliModule(Module):
    """Assemble profile ports without bootstrapping or opening a database."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace

    def configure(self, binder: Binder) -> None:
        connection = SqliteSchemaConnectionFactory(
            Path(self._workspace),
            load_workspace_migrations(with_runtime=True),
            minimum_version=2,
        )
        store = SqliteWatchProfileStoreAdapter(connection.connect)
        normalize = NormalizeWatchConfiguration(frozenset({"arxiv", "pubmed", "crossref"}))
        binder.bind(ImportDomainSeedsPort, to=InstanceProvider(ImportDomainSeeds(normalize, store)))
        binder.bind(PublishWatchProfilePort, to=InstanceProvider(PublishWatchProfile(normalize, store)))
        binder.bind(ReadWatchProfilePort, to=InstanceProvider(ReadWatchProfile(store)))
        binder.bind(ReadDomainDefinitionPort, to=InstanceProvider(ReadDomainDefinition(store)))
        binder.bind(SetWatchProfileLifecyclePort, to=InstanceProvider(SetWatchProfileLifecycle(store)))


def _harvest_configuration(
    binder: Binder,
    workspace: str,
) -> tuple[
    Path,
    SqliteSchemaConnectionFactory,
    SqliteWatchProfileStoreAdapter,
    ArxivQueryCompilerAdapter,
]:
    root = Path(workspace)
    connection = SqliteSchemaConnectionFactory(
        root,
        load_workspace_migrations(with_runtime=True),
        minimum_version=4,
    )
    store = SqliteWatchProfileStoreAdapter(connection.connect)
    compiler = ArxivQueryCompilerAdapter()
    binder.bind(
        BuildHarvestQueryInputPort,
        to=InstanceProvider(
            BuildHarvestQueryInput(
                ReadWatchProfile(store),
                ReadDomainDefinition(store),
            )
        ),
    )
    return root, connection, store, compiler


class HarvestPlanCliModule(Module):
    """Compile a published configuration without external I/O."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace

    def configure(self, binder: Binder) -> None:
        _, _, _, compiler = _harvest_configuration(binder, self._workspace)
        binder.bind(
            CompileSourceQueryPort,
            to=InstanceProvider(CompileSourceQuery(compiler)),
        )


def _compose_harvest_runner(
    root: Path,
    connection: SqliteSchemaConnectionFactory,
    store: SqliteWatchProfileStoreAdapter,
    compiler: ArxivQueryCompilerAdapter,
    rate_limit_state: str,
    transport: SourceHttpTransportPort | None,
) -> RunHarvestSlice:
    journal = SqliteHarvestStoreAdapter(connection.connect, compiler)
    files = FilesystemObjectBytesAdapter(root)
    raw_connection = SqliteConnectionFactory(root)
    objects = SqliteObjectUnitOfWorkAdapter(raw_connection)
    processing = SqliteHarvestProcessingAdapter(connection.connect)
    parser = ParseSourcePage(ArxivAtomParserAdapter())
    process = ProcessHarvestPage(
        ReadHarvestAttempt(journal),
        ReadObject(files, objects),
        parser,
        processing,
        PARSER_VERSION,
    )
    source_transport = transport or HttpClientArxivTransportAdapter(enabled=True)
    source = FetchSourcePage(
        ArxivSourceAdapter(
            source_transport,
            PosixArxivRateLimitAdapter(Path(rate_limit_state)),
            enabled=True,
        )
    )
    runtime = SystemHarvestRuntimeAdapter()
    return RunHarvestSlice(
        compiler,
        ReadHarvestResume(SqliteHarvestResumeAdapter(connection.connect, compiler)),
        StartHarvestAttempt(journal),
        source,
        RecordHarvestCapture(journal, PublishObject(files, objects)),
        process,
        ReadWatchProfile(store),
        runtime,
        PARSER_VERSION,
    )


class HarvestRunCliModule(Module):
    """Compose the bounded live collector; constructing this module performs no external I/O."""

    def __init__(
        self,
        workspace: str,
        rate_limit_state: str,
        *,
        transport: SourceHttpTransportPort | None = None,
    ) -> None:
        self._workspace = workspace
        self._rate_limit_state = rate_limit_state
        self._transport = transport

    def configure(self, binder: Binder) -> None:
        root, connection, store, compiler = _harvest_configuration(binder, self._workspace)
        runner = _compose_harvest_runner(
            root,
            connection,
            store,
            compiler,
            self._rate_limit_state,
            self._transport,
        )
        binder.bind(RunHarvestSlicePort, to=InstanceProvider(runner))


class WorkerCliModule(Module):
    """Assemble scheduler and one-job execution without performing external I/O."""

    def __init__(
        self,
        workspace: str,
        *,
        allow_live_source: bool = False,
        rate_limit_state: str | None = None,
        transport: SourceHttpTransportPort | None = None,
        ncbi_email: str | None = None,
        ncbi_api_key: str | None = None,
        ncbi_rate_limit_state: str | None = None,
        ncbi_transport: SourceHttpTransportPort | None = None,
        crossref_email: str | None = None,
        crossref_rate_limit_dir: str | None = None,
        crossref_transport: CrossrefHttpTransportPort | None = None,
        allow_live_mail: bool = False,
        recipient_map_json: str | None = None,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_sender: str | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        mail_sender: MailSenderPort | None = None,
        recipient_resolver: RecipientResolverPort | None = None,
        explanation: ProcessEvidenceExplanationPort | None = None,
        structured_generation: StructuredGenerationPort | None = None,
        generation_budget_policy: GenerationBudgetPolicy | None = None,
    ) -> None:
        self._workspace = workspace
        self._allow_live_source = allow_live_source
        self._rate_limit_state = rate_limit_state
        self._transport = transport
        self._ncbi_email = ncbi_email
        self._ncbi_api_key = ncbi_api_key
        self._ncbi_rate_limit_state = ncbi_rate_limit_state
        self._ncbi_transport = ncbi_transport
        self._crossref_email = crossref_email
        self._crossref_rate_limit_dir = crossref_rate_limit_dir
        self._crossref_transport = crossref_transport
        self._allow_live_mail = allow_live_mail
        self._recipient_map_json = recipient_map_json
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_sender = smtp_sender
        self._smtp_username = smtp_username
        self._smtp_password = smtp_password
        self._mail_sender = mail_sender
        self._recipient_resolver = recipient_resolver
        self._explanation = explanation
        self._structured_generation = structured_generation
        self._generation_budget_policy = generation_budget_policy

    def configure(self, binder: Binder) -> None:
        root = Path(self._workspace)
        tracked_generation_requested = (
            self._structured_generation is not None
            or self._generation_budget_policy is not None
        )
        if (
            (self._structured_generation is None)
            != (self._generation_budget_policy is None)
            or (self._explanation is not None and tracked_generation_requested)
        ):
            raise ValueError("invalid_explanation_runtime_configuration")
        crossref_requested = (
            self._allow_live_source
            and self._crossref_email is not None
            and self._crossref_rate_limit_dir is not None
        )
        arxiv_requested = (
            self._allow_live_source
            and self._rate_limit_state is not None
        )
        pubmed_requested = (
            self._allow_live_source
            and self._ncbi_email is not None
            and self._ncbi_rate_limit_state is not None
        )
        source_projection_requested = arxiv_requested or pubmed_requested
        minimum_version = 22 if crossref_requested else 10
        if self._allow_live_mail:
            minimum_version = max(minimum_version, 23)
        if source_projection_requested:
            minimum_version = max(minimum_version, 24)
        if self._explanation is not None or tracked_generation_requested:
            minimum_version = max(minimum_version, 24)
        connection = SqliteSchemaConnectionFactory(
            root,
            load_workspace_migrations(with_runtime=True),
            minimum_version=minimum_version,
        )
        profile_store = SqliteWatchProfileStoreAdapter(connection.connect)
        builder = BuildHarvestQueryInput(
            ReadWatchProfile(profile_store),
            ReadDomainDefinition(profile_store),
        )
        compiler = ArxivQueryCompilerAdapter()

        files = FilesystemObjectBytesAdapter(root)
        object_connection = SqliteConnectionFactory(root)
        objects = SqliteObjectUnitOfWorkAdapter(object_connection)
        read_object = ReadObject(files, objects)
        publish_object = PublishObject(files, objects)
        evidence_objects = KernelEvidenceObjectAdapter(
            publish_object,
            read_object,
        )

        clock = SystemWorkflowClockAdapter()
        jobs = SqliteWorkflowJobStoreAdapter(connection.connect)

        harvest = None
        if self._allow_live_source and self._rate_limit_state is not None:
            harvest = _compose_harvest_runner(
                root,
                connection,
                profile_store,
                compiler,
                self._rate_limit_state,
                self._transport,
            )

        pubmed = None
        if (
            self._allow_live_source
            and self._ncbi_email is not None
            and self._ncbi_rate_limit_state is not None
        ):
            pubmed_source = PubmedSourceAdapter(
                tool="paper-radar",
                email=self._ncbi_email,
                api_key=self._ncbi_api_key,
            )
            ncbi_transport = RateLimitedSourceHttpTransportAdapter(
                self._ncbi_transport or HttpClientNcbiTransportAdapter(enabled=True),
                PosixNcbiRateLimitAdapter(
                    Path(self._ncbi_rate_limit_state),
                    api_key_present=self._ncbi_api_key is not None,
                ),
            )
            pubmed = RunPubmedHarvestWindow(
                pubmed_source,
                ncbi_transport,
                publish_object,
                SqlitePubmedHarvestStoreAdapter(connection.connect),
            )

        source_catalog = None
        if source_projection_requested:
            identity_store = SqlitePaperIdentityStoreAdapter(connection.connect)
            resolve_identity = ResolvePaperIdentity(
                NormalizePaperIdentifier(),
                identity_store,
            )
            record_revision = RecordPaperRevision(
                SqliteResearchEventStoreAdapter(connection.connect)
            )
            abstract_evidence = PrepareAbstractEvidence(
                PrepareEvidenceSnapshot(
                    EvidenceSnapshotRules(),
                    evidence_objects,
                    SqliteEvidenceSnapshotStoreAdapter(connection.connect),
                )
            )
            source_catalog = ProjectSourceCatalogUnit(
                SqliteSourceObservationPageAdapter(connection.connect),
                SqliteSourceCatalogProjectionStoreAdapter(connection.connect),
                SqliteArxivObservationReplayAdapter(
                    connection.connect,
                    read_object,
                ),
                ProjectArxivObservation(
                    resolve_identity,
                    record_revision,
                    abstract_evidence,
                ),
                SqlitePubmedObservationReplayAdapter(
                    connection.connect,
                    read_object,
                ),
                ProjectPubmedObservation(
                    resolve_identity,
                    record_revision,
                    abstract_evidence,
                ),
                SqliteHarvestUnitContextAdapter(connection.connect),
                jobs,
            )

        explanation = self._explanation
        if tracked_generation_requested:
            assert self._structured_generation is not None
            assert self._generation_budget_policy is not None
            evidence_reader = ReadEvidenceSnapshot(
                EvidenceSnapshotRules(),
                evidence_objects,
                SqliteEvidenceSnapshotStoreAdapter(connection.connect),
            )
            generation = RunBudgetedGeneration(
                self._structured_generation,
                SqliteGenerationLedgerAdapter(
                    connection.connect,
                    KernelGenerationOutputAdapter(
                        publish_object,
                        read_object,
                    ),
                ),
                clock.now,
                self._generation_budget_policy,
            )
            explanation = ProcessEvidenceExplanation(
                ExtractTrackedPaperClaims(
                    evidence_reader,
                    generation,
                ),
                AssessTrackedPaperRelevance(
                    ReadWatchProfile(profile_store),
                    ReadDomainDefinition(profile_store),
                    generation,
                ),
                PersistRelevanceAssessment(
                    SqliteRelevanceAssessmentStoreAdapter(
                        connection.connect
                    )
                ),
                GenerateTrackedReadingCard(
                    evidence_reader,
                    generation,
                ),
                VerifyTrackedExplanation(generation),
                PersistVerifiedExplanation(
                    KernelExplanationArtifactAdapter(publish_object),
                    SqliteVerifiedExplanationStoreAdapter(
                        connection.connect
                    ),
                ),
                PublishVerifiedCurrentSummary(
                    SqliteCurrentSummaryStoreAdapter(connection.connect)
                ),
                clock,
            )

        crossref_plan = None
        crossref = None
        if crossref_requested:
            assert self._crossref_email is not None
            assert self._crossref_rate_limit_dir is not None
            crossref_source = CrossrefSourceAdapter()
            crossref_gate = PosixCrossrefRateGateAdapter(
                Path(self._crossref_rate_limit_dir),
                self._crossref_email,
            )
            crossref_transport = (
                self._crossref_transport
                or HttpClientCrossrefTransportAdapter(
                    contact_email=self._crossref_email,
                    enabled=True,
                )
            )
            crossref_claims = SqliteCrossrefCaptureClaimStoreAdapter(
                connection.connect
            )
            crossref_inbox = SqliteCrossrefCaptureInboxAdapter(
                connection.connect
            )
            crossref_captures = KernelCrossrefCaptureStoreAdapter(
                publish_object,
                read_object,
            )
            crossref_publisher = PublishClaimedCrossrefCapture(
                crossref_inbox,
                crossref_captures,
            )
            crossref_attachments = SqliteClaimedCrossrefAttachmentAdapter(
                connection.connect
            )
            crossref_capture = CaptureClaimedCrossrefResponse(
                crossref_transport,
                crossref_gate,
                crossref_claims,
                crossref_inbox,
                crossref_publisher,
                source=crossref_source,
                clock=clock.now,
            )
            crossref_recover = AttachClaimedCrossrefCapture(
                crossref_publisher,
                crossref_gate,
                crossref_attachments,
                source=crossref_source,
                clock=clock.now,
            )
            crossref_resolve = ResolveClaimedCrossrefCapture(
                crossref_publisher,
                crossref_attachments,
                source=crossref_source,
                clock=clock.now,
            )
            crossref_journal = SqliteCrossrefHarvestJournalAdapter(
                connection.connect
            )
            claimed_crossref = RunClaimedCrossrefHarvestWindow(
                crossref_source,
                crossref_journal,
                crossref_claims,
                crossref_inbox,
                crossref_capture,
                crossref_attachments,
                crossref_recover,
                crossref_resolve,
                ReplayCrossrefCapture(
                    crossref_captures,
                    crossref_source,
                ),
                SqliteWorkspaceInfoAdapter(connection.connect),
                clock=clock.now,
            )

            normalize_identifier = NormalizePaperIdentifier()
            identity_store = SqlitePaperIdentityStoreAdapter(connection.connect)
            integrity_bindings = BindCrossrefIntegrityWorks(
                ReadPaperIdentity(
                    normalize_identifier,
                    identity_store,
                ),
                SqliteCrossrefIntegrityWorkBindingStoreAdapter(
                    connection.connect
                ),
            )
            integrity_events = PromoteCrossrefIntegrityEvents(
                SqliteCrossrefIntegrityEventSourceAdapter(
                    connection.connect
                ),
                RecordPaperRevision(
                    SqliteResearchEventStoreAdapter(
                        connection.connect
                    )
                ),
            )
            project_crossref = ProjectCrossrefPendingItem(
                normalize_identifier,
                SqliteCrossrefProviderRevisionStoreAdapter(
                    connection.connect
                ),
                SqliteCrossrefRelationStoreAdapter(
                    connection.connect
                ),
                integrity=SqliteCrossrefIntegrityStoreAdapter(
                    connection.connect
                ),
                integrity_bindings=integrity_bindings,
                integrity_events=integrity_events,
            )
            crossref = RunProjectedCrossrefHarvestWindow(
                claimed_crossref,
                crossref_journal,
                project_crossref,
                clock=clock.now,
            )
            crossref_plan = BuildCrossrefWindowPlan(
                crossref_source,
                contact_email=self._crossref_email,
            )
        delivery_store = SqliteDeliveryStoreAdapter(connection.connect)
        digest_artifacts = KernelDigestArtifactAdapter(publish_object, read_object)
        current_summaries = SqliteDigestCurrentSummaryAdapter(
            connection.connect,
            read_object,
        )
        prior_recipient = SqlitePriorRecipientHistoryAdapter(
            connection.connect
        )
        scheduled_digest = PrepareScheduledDigest(
            events=SqliteDigestResearchEventAdapter(connection.connect),
            summaries=current_summaries,
            relevance=SqliteDigestRelevanceAdapter(connection.connect),
            context=SqliteDigestDeliveryContextAdapter(connection.connect),
            queue=QueueDigest(digest_artifacts, delivery_store),
            prior_recipient=prior_recipient,
        )
        mail_dispatch = None
        if self._allow_live_mail:
            injected = (
                self._mail_sender is not None
                or self._recipient_resolver is not None
            )
            if injected:
                if (
                    self._mail_sender is None
                    or self._recipient_resolver is None
                ):
                    raise MailConfigurationError(
                        "incomplete_mail_runtime_override"
                    )
                sender = self._mail_sender
                recipients = self._recipient_resolver
            else:
                if (
                    self._recipient_map_json is None
                    or self._smtp_host is None
                    or self._smtp_port is None
                    or self._smtp_sender is None
                    or self._smtp_username is None
                    or self._smtp_password is None
                ):
                    raise MailConfigurationError(
                        "incomplete_mail_configuration"
                    )
                recipients = JsonRecipientResolverAdapter(
                    self._recipient_map_json
                )
                sender = SmtpMailSenderAdapter(
                    host=self._smtp_host,
                    port=self._smtp_port,
                    sender=self._smtp_sender,
                    username=self._smtp_username,
                    password=self._smtp_password,
                )
            mail_dispatch = DispatchDigest(
                store=delivery_store,
                artifacts=digest_artifacts,
                recipients=recipients,
                sender=sender,
            )
        revision_notice = ProcessRevisionNotice(
            store=delivery_store,
            summaries=current_summaries,
            prior_recipient=prior_recipient,
            dispatch=mail_dispatch,
            prepare=scheduled_digest,
            clock=clock.now,
        )

        scheduler = RunSchedulerTick(
            SqliteSchedulerInputAdapter(connection.connect),
            jobs,
        )
        processor = ProcessWorkflowJob(
            store=jobs,
            builder=builder,
            harvest=harvest,
            clock=clock,
            live_source_enabled=self._allow_live_source,
            digest=scheduled_digest,
            pubmed=pubmed,
            crossref_plan=crossref_plan,
            crossref=crossref,
            revision_notice=revision_notice,
            source_catalog=source_catalog,
            explanation=explanation,
        )
        cycle = RunWorkerCycle(scheduler, processor, clock)

        binder.bind(WorkflowJobStorePort, to=InstanceProvider(jobs))
        binder.bind(WorkflowClockPort, to=InstanceProvider(clock))
        binder.bind(RunSchedulerTickPort, to=InstanceProvider(scheduler))
        binder.bind(PrepareScheduledDigestPort, to=InstanceProvider(scheduled_digest))
        binder.bind(ProcessRevisionNoticePort, to=InstanceProvider(revision_notice))
        if mail_dispatch is not None:
            binder.bind(DispatchDigestPort, to=InstanceProvider(mail_dispatch))
        if pubmed is not None:
            binder.bind(RunPubmedHarvestWindowPort, to=InstanceProvider(pubmed))
        if source_catalog is not None:
            binder.bind(
                ProjectSourceCatalogUnitPort,
                to=InstanceProvider(source_catalog),
            )
        if explanation is not None:
            binder.bind(
                ProcessEvidenceExplanationPort,
                to=InstanceProvider(explanation),
            )
        if crossref is not None and crossref_plan is not None:
            binder.bind(
                BuildCrossrefWindowPlanPort,
                to=InstanceProvider(crossref_plan),
            )
            binder.bind(
                RunCrossrefHarvestWindowPort,
                to=InstanceProvider(crossref),
            )
        binder.bind(ProcessWorkflowJobPort, to=InstanceProvider(processor))
        binder.bind(RunWorkerCyclePort, to=InstanceProvider(cycle))
