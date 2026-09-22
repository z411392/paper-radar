from pathlib import Path

from injector import Binder, InstanceProvider, Module, singleton

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.initialize_workspace import InitializeWorkspace
from libs.kernel.ports.initialize_workspace_port import InitializeWorkspacePort
from libs.kernel.ports.workspace_bootstrap_port import WorkspaceBootstrapPort
from libs.research_workflow.adapters.driven.python_runtime_version_adapter import PythonRuntimeVersionAdapter
from libs.research_workflow.application.queries.read_runtime_version import ReadRuntimeVersion
from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort
from libs.research_workflow.ports.runtime_version_provider_port import RuntimeVersionProviderPort
from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter import (
    SqliteWatchProfileStoreAdapter,
)
from libs.watch_profiles.application.commands.import_domain_seeds import ImportDomainSeeds
from libs.watch_profiles.application.commands.publish_watch_profile import PublishWatchProfile
from libs.watch_profiles.application.commands.set_watch_profile_lifecycle import SetWatchProfileLifecycle
from libs.watch_profiles.application.queries.read_watch_profile import ReadWatchProfile
from libs.watch_profiles.domain.services.normalize_watch_configuration import NormalizeWatchConfiguration
from libs.watch_profiles.ports.import_domain_seeds_port import ImportDomainSeedsPort
from libs.watch_profiles.ports.publish_watch_profile_port import PublishWatchProfilePort
from libs.watch_profiles.ports.read_watch_profile_port import ReadWatchProfilePort
from libs.watch_profiles.ports.set_watch_profile_lifecycle_port import SetWatchProfileLifecyclePort


class CliModule(Module):
    def __init__(self, workspace: str | None = None, *, with_profiles: bool = False) -> None:
        self._workspace = workspace
        self._with_profiles = with_profiles

    def configure(self, binder: Binder) -> None:
        binder.bind(RuntimeVersionProviderPort, to=PythonRuntimeVersionAdapter, scope=singleton)
        binder.bind(ReadRuntimeVersionPort, to=ReadRuntimeVersion, scope=singleton)
        if self._workspace is not None:
            bootstrap = SqliteWorkspaceBootstrapAdapter(
                Path(self._workspace), load_workspace_migrations(with_profiles=self._with_profiles)
            )
            binder.bind(WorkspaceBootstrapPort, to=InstanceProvider(bootstrap), scope=singleton)
            command = InitializeWorkspace(bootstrap)
            binder.bind(InitializeWorkspacePort, to=InstanceProvider(command), scope=singleton)


class WatchProfileCliModule(Module):
    """Assemble existing feature ports without bootstrapping or opening a database."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace

    def configure(self, binder: Binder) -> None:
        connection = SqliteSchemaConnectionFactory(
            Path(self._workspace), load_workspace_migrations(with_profiles=True)
        )
        store = SqliteWatchProfileStoreAdapter(connection.connect)
        normalize = NormalizeWatchConfiguration(frozenset({"arxiv", "pubmed", "crossref"}))
        binder.bind(ImportDomainSeedsPort, to=InstanceProvider(ImportDomainSeeds(normalize, store)))
        binder.bind(PublishWatchProfilePort, to=InstanceProvider(PublishWatchProfile(normalize, store)))
        binder.bind(ReadWatchProfilePort, to=InstanceProvider(ReadWatchProfile(store)))
        binder.bind(SetWatchProfileLifecyclePort, to=InstanceProvider(SetWatchProfileLifecycle(store)))
