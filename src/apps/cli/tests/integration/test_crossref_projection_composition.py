from injector import Injector

from apps.cli.module import WorkerCliModule
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.research_workflow.application.commands.run_projected_crossref_harvest_window import (
    RunProjectedCrossrefHarvestWindow,
)
from libs.research_workflow.ports.run_crossref_harvest_window_port import (
    RunCrossrefHarvestWindowPort,
)


def test_commissioned_crossref_injects_projected_runner_without_provider_io(
    tmp_path,
) -> None:
    workspace = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(
        workspace,
        migrations,
    ).initialize()
    assert info.schema_version == 22
    rate_dir = tmp_path / "crossref-rate"
    rate_dir.mkdir(mode=0o700)

    injector = Injector(
        [
            WorkerCliModule(
                str(workspace),
                allow_live_source=True,
                crossref_email="fixture@example.invalid",
                crossref_rate_limit_dir=str(rate_dir),
            )
        ],
        auto_bind=False,
    )

    runner = injector.get(RunCrossrefHarvestWindowPort)

    assert isinstance(runner, RunProjectedCrossrefHarvestWindow)
