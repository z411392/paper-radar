from pathlib import Path

from injector import Binder, InstanceProvider, Module, singleton

from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import PARSER_VERSION, ArxivAtomParserAdapter
from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.adapters.driven.arxiv_source_adapter import ArxivSourceAdapter
from libs.discovery.adapters.driven.http_client_arxiv_transport_adapter import HttpClientArxivTransportAdapter
from libs.discovery.adapters.driven.posix_arxiv_rate_limit_adapter import PosixArxivRateLimitAdapter
from libs.discovery.adapters.driven.sqlite_harvest_processing_adapter import SqliteHarvestProcessingAdapter
from libs.discovery.adapters.driven.sqlite_harvest_resume_adapter import SqliteHarvestResumeAdapter
from libs.discovery.adapters.driven.sqlite_harvest_store_adapter import SqliteHarvestStoreAdapter
from libs.discovery.application.commands.process_harvest_page import ProcessHarvestPage
from libs.discovery.application.commands.record_harvest_capture import RecordHarvestCapture
from libs.discovery.application.commands.start_harvest_attempt import StartHarvestAttempt
from libs.discovery.application.queries.compile_source_query import CompileSourceQuery
from libs.discovery.application.queries.fetch_source_page import FetchSourcePage
from libs.discovery.application.queries.parse_source_page import ParseSourcePage
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
from libs.discovery.application.queries.read_harvest_resume import ReadHarvestResume
from libs.discovery.ports.compile_source_query_port import CompileSourceQueryPort
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
from libs.research_workflow.adapters.driven.python_runtime_version_adapter import PythonRuntimeVersionAdapter
from libs.research_workflow.adapters.driven.system_harvest_runtime_adapter import SystemHarvestRuntimeAdapter
from libs.research_workflow.application.commands.run_harvest_slice import RunHarvestSlice
from libs.research_workflow.application.queries.build_harvest_query_input import BuildHarvestQueryInput
from libs.research_workflow.application.queries.read_runtime_version import ReadRuntimeVersion
from libs.research_workflow.ports.build_harvest_query_input_port import BuildHarvestQueryInputPort
from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort
from libs.research_workflow.ports.run_harvest_slice_port import RunHarvestSlicePort
from libs.research_workflow.ports.runtime_version_provider_port import RuntimeVersionProviderPort
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
        transport = self._transport or HttpClientArxivTransportAdapter(enabled=True)
        source = FetchSourcePage(
            ArxivSourceAdapter(
                transport,
                PosixArxivRateLimitAdapter(Path(self._rate_limit_state)),
                enabled=True,
            )
        )
        runtime = SystemHarvestRuntimeAdapter()
        runner = RunHarvestSlice(
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
        binder.bind(RunHarvestSlicePort, to=InstanceProvider(runner))
