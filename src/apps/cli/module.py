from pathlib import Path

from injector import Binder, InstanceProvider, Module, singleton

from libs.delivery.adapters.driven.kernel_digest_artifact_adapter import KernelDigestArtifactAdapter
from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import SqliteDeliveryStoreAdapter
from libs.delivery.adapters.driven.sqlite_digest_delivery_context_adapter import (
    SqliteDigestDeliveryContextAdapter,
)
from libs.delivery.application.commands.prepare_scheduled_digest import PrepareScheduledDigest
from libs.delivery.application.commands.queue_digest import QueueDigest
from libs.delivery.ports.prepare_scheduled_digest_port import PrepareScheduledDigestPort
from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import PARSER_VERSION, ArxivAtomParserAdapter
from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.adapters.driven.arxiv_source_adapter import ArxivSourceAdapter
from libs.discovery.adapters.driven.http_client_arxiv_transport_adapter import HttpClientArxivTransportAdapter
from libs.discovery.adapters.driven.http_client_ncbi_transport_adapter import HttpClientNcbiTransportAdapter
from libs.discovery.adapters.driven.posix_arxiv_rate_limit_adapter import PosixArxivRateLimitAdapter
from libs.discovery.adapters.driven.posix_ncbi_rate_limit_adapter import PosixNcbiRateLimitAdapter
from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.adapters.driven.rate_limited_source_http_transport_adapter import (
    RateLimitedSourceHttpTransportAdapter,
)
from libs.discovery.adapters.driven.sqlite_harvest_processing_adapter import SqliteHarvestProcessingAdapter
from libs.discovery.adapters.driven.sqlite_harvest_resume_adapter import SqliteHarvestResumeAdapter
from libs.discovery.adapters.driven.sqlite_harvest_store_adapter import SqliteHarvestStoreAdapter
from libs.discovery.adapters.driven.sqlite_pubmed_harvest_store_adapter import (
    SqlitePubmedHarvestStoreAdapter,
)
from libs.discovery.application.commands.process_harvest_page import ProcessHarvestPage
from libs.discovery.application.commands.record_harvest_capture import RecordHarvestCapture
from libs.discovery.application.commands.run_pubmed_harvest_window import RunPubmedHarvestWindow
from libs.discovery.application.commands.start_harvest_attempt import StartHarvestAttempt
from libs.discovery.application.queries.compile_source_query import CompileSourceQuery
from libs.discovery.application.queries.fetch_source_page import FetchSourcePage
from libs.discovery.application.queries.parse_source_page import ParseSourcePage
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
from libs.discovery.application.queries.read_harvest_resume import ReadHarvestResume
from libs.discovery.ports.compile_source_query_port import CompileSourceQueryPort
from libs.discovery.ports.run_pubmed_harvest_window_port import RunPubmedHarvestWindowPort
from libs.discovery.ports.source_http_transport_port import SourceHttpTransportPort
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.initialize_workspace import InitializeWorkspace
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject
from libs.kernel.ports.initialize_workspace_port import InitializeWorkspacePort
from libs.kernel.ports.workspace_bootstrap_port import WorkspaceBootstrapPort
from libs.paper_explanations.adapters.driven.sqlite_digest_current_summary_adapter import (
    SqliteDigestCurrentSummaryAdapter,
)
from libs.research_workflow.adapters.driven.python_runtime_version_adapter import PythonRuntimeVersionAdapter
from libs.research_workflow.adapters.driven.sqlite_scheduler_input_adapter import SqliteSchedulerInputAdapter
from libs.research_workflow.adapters.driven.sqlite_workflow_job_store_adapter import (
    SqliteWorkflowJobStoreAdapter,
)
from libs.research_workflow.adapters.driven.system_harvest_runtime_adapter import SystemHarvestRuntimeAdapter
from libs.research_workflow.adapters.driven.system_workflow_clock_adapter import SystemWorkflowClockAdapter
from libs.research_workflow.application.commands.process_workflow_job import ProcessWorkflowJob
from libs.research_workflow.application.commands.run_harvest_slice import RunHarvestSlice
from libs.research_workflow.application.commands.run_scheduler_tick import RunSchedulerTick
from libs.research_workflow.application.commands.run_worker_cycle import RunWorkerCycle
from libs.research_workflow.application.queries.build_harvest_query_input import BuildHarvestQueryInput
from libs.research_workflow.application.queries.read_runtime_version import ReadRuntimeVersion
from libs.research_workflow.ports.build_harvest_query_input_port import BuildHarvestQueryInputPort
from libs.research_workflow.ports.process_workflow_job_port import ProcessWorkflowJobPort
from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort
from libs.research_workflow.ports.run_harvest_slice_port import RunHarvestSlicePort
from libs.research_workflow.ports.run_scheduler_tick_port import RunSchedulerTickPort
from libs.research_workflow.ports.run_worker_cycle_port import RunWorkerCyclePort
from libs.research_workflow.ports.runtime_version_provider_port import RuntimeVersionProviderPort
from libs.research_workflow.ports.workflow_clock_port import WorkflowClockPort
from libs.research_workflow.ports.workflow_job_store_port import WorkflowJobStorePort
from libs.scholarly_catalog.adapters.driven.sqlite_digest_research_event_adapter import (
    SqliteDigestResearchEventAdapter,
)
from libs.watch_profiles.adapters.driven.sqlite_digest_relevance_adapter import (
    SqliteDigestRelevanceAdapter,
)
from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter import (
    SqliteWatchProfileStoreAdapter,
)
from libs.watch_profiles.application.commands.import_domain_seeds import ImportDomainSeeds
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
        self._ncbi_email = ncbi_email
        self._ncbi_api_key = ncbi_api_key
        self._ncbi_rate_limit_state = ncbi_rate_limit_state
        self._ncbi_transport = ncbi_transport

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
    ) -> None:
        self._workspace = workspace
        self._allow_live_source = allow_live_source
        self._rate_limit_state = rate_limit_state
        self._transport = transport

    def configure(self, binder: Binder) -> None:
        root = Path(self._workspace)
        connection = SqliteSchemaConnectionFactory(
            root,
            load_workspace_migrations(with_runtime=True),
            minimum_version=10,
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
        delivery_store = SqliteDeliveryStoreAdapter(connection.connect)
        digest_artifacts = KernelDigestArtifactAdapter(publish_object, read_object)
        scheduled_digest = PrepareScheduledDigest(
            events=SqliteDigestResearchEventAdapter(connection.connect),
            summaries=SqliteDigestCurrentSummaryAdapter(connection.connect, read_object),
            relevance=SqliteDigestRelevanceAdapter(connection.connect),
            context=SqliteDigestDeliveryContextAdapter(connection.connect),
            queue=QueueDigest(digest_artifacts, delivery_store),
        )

        jobs = SqliteWorkflowJobStoreAdapter(connection.connect)
        clock = SystemWorkflowClockAdapter()
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
        )
        cycle = RunWorkerCycle(scheduler, processor, clock)

        binder.bind(WorkflowJobStorePort, to=InstanceProvider(jobs))
        binder.bind(WorkflowClockPort, to=InstanceProvider(clock))
        binder.bind(RunSchedulerTickPort, to=InstanceProvider(scheduler))
        binder.bind(PrepareScheduledDigestPort, to=InstanceProvider(scheduled_digest))
        if pubmed is not None:
            binder.bind(RunPubmedHarvestWindowPort, to=InstanceProvider(pubmed))
        binder.bind(ProcessWorkflowJobPort, to=InstanceProvider(processor))
        binder.bind(RunWorkerCyclePort, to=InstanceProvider(cycle))
