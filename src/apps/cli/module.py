from pathlib import Path

from injector import Binder, InstanceProvider, Module, singleton

from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.application.queries.compile_source_query import CompileSourceQuery
from libs.discovery.ports.compile_source_query_port import CompileSourceQueryPort
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.initialize_workspace import InitializeWorkspace
from libs.kernel.ports.initialize_workspace_port import InitializeWorkspacePort
from libs.kernel.ports.workspace_bootstrap_port import WorkspaceBootstrapPort
from libs.research_workflow.adapters.driven.python_runtime_version_adapter import PythonRuntimeVersionAdapter
from libs.research_workflow.application.queries.build_harvest_query_input import BuildHarvestQueryInput
from libs.research_workflow.application.queries.read_runtime_version import ReadRuntimeVersion
from libs.research_workflow.ports.build_harvest_query_input_port import BuildHarvestQueryInputPort
from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort
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
    ) -> None:
        self._workspace = workspace
        self._with_profiles = with_profiles
        self._with_discovery = with_discovery

    def configure(self, binder: Binder) -> None:
        binder.bind(RuntimeVersionProviderPort, to=PythonRuntimeVersionAdapter, scope=singleton)
        binder.bind(ReadRuntimeVersionPort, to=ReadRuntimeVersion, scope=singleton)
        if self._workspace is not None:
            bootstrap = SqliteWorkspaceBootstrapAdapter(
                Path(self._workspace),
                load_workspace_migrations(
                    with_profiles=self._with_profiles,
                    with_discovery=self._with_discovery,
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
            load_workspace_migrations(with_discovery=True),
            minimum_version=2,
        )
        store = SqliteWatchProfileStoreAdapter(connection.connect)
        normalize = NormalizeWatchConfiguration(frozenset({"arxiv", "pubmed", "crossref"}))
        binder.bind(ImportDomainSeedsPort, to=InstanceProvider(ImportDomainSeeds(normalize, store)))
        binder.bind(PublishWatchProfilePort, to=InstanceProvider(PublishWatchProfile(normalize, store)))
        binder.bind(ReadWatchProfilePort, to=InstanceProvider(ReadWatchProfile(store)))
        binder.bind(ReadDomainDefinitionPort, to=InstanceProvider(ReadDomainDefinition(store)))
        binder.bind(SetWatchProfileLifecyclePort, to=InstanceProvider(SetWatchProfileLifecycle(store)))


class HarvestPlanCliModule(Module):
    """Read published configuration and compile a source plan without external I/O."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace

    def configure(self, binder: Binder) -> None:
        connection = SqliteSchemaConnectionFactory(
            Path(self._workspace),
            load_workspace_migrations(with_discovery=True),
            minimum_version=4,
        )
        store = SqliteWatchProfileStoreAdapter(connection.connect)
        profiles = ReadWatchProfile(store)
        domains = ReadDomainDefinition(store)
        compiler = ArxivQueryCompilerAdapter()
        binder.bind(
            BuildHarvestQueryInputPort,
            to=InstanceProvider(BuildHarvestQueryInput(profiles, domains)),
        )
        binder.bind(
            CompileSourceQueryPort,
            to=InstanceProvider(CompileSourceQuery(compiler)),
        )
