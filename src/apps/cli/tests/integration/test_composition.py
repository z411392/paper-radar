import json
from pathlib import Path

import pytest
from injector import Injector, UnsatisfiedRequirement

from apps.cli.adapters.driving.show_version import show_version
from apps.cli.module import CliModule, OperationalHealthCliModule, WorkerCliModule
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.paper_explanations.dtos.generation_budget_policy import (
    GenerationBudgetPolicy,
)
from libs.research_workflow.ports.operational_health_port import InspectHealthPort
from libs.research_workflow.ports.process_evidence_explanation_port import (
    ProcessEvidenceExplanationPort,
)
from libs.research_workflow.ports.process_revision_notice_port import (
    ProcessRevisionNoticePort,
)
from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort


def test_production_composition_resolves_inbound_port() -> None:
    injector = Injector([CliModule()], auto_bind=False)
    assert injector.get(ReadRuntimeVersionPort)().package_name == "paper-radar"


def test_driving_adapter_prints_only_public_version_fields(capsys: pytest.CaptureFixture[str]) -> None:
    show_version(Injector([CliModule()], auto_bind=False))
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {"package_name", "package_version", "python_version"}


def test_missing_binding_is_not_silently_autowired() -> None:
    with pytest.raises(UnsatisfiedRequirement):
        Injector(auto_bind=False).get(ReadRuntimeVersionPort)


def test_worker_composition_exposes_revision_notice_preflight_without_mail_sender(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        root,
        load_workspace_migrations(with_runtime=True),
    ).initialize()
    injector = Injector([WorkerCliModule(str(root))])

    assert injector.get(ProcessRevisionNoticePort) is not None


def test_worker_mail_override_requires_sender_and_recipient_resolver(
    tmp_path: Path,
) -> None:
    from libs.delivery.exceptions.mail_configuration_error import (
        MailConfigurationError,
    )

    root = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        root,
        load_workspace_migrations(with_runtime=True),
    ).initialize()

    with pytest.raises(
        MailConfigurationError,
        match="incomplete_mail_runtime_override",
    ):
        Injector(
            [
                WorkerCliModule(
                    str(root),
                    allow_live_mail=True,
                    mail_sender=object(),
                )
            ],
            auto_bind=False,
        )


def test_worker_mail_override_exposes_dispatch_port_without_network(
    tmp_path: Path,
) -> None:
    from libs.delivery.ports.dispatch_digest_port import DispatchDigestPort

    class Sender:
        def send(self, message):
            raise AssertionError("composition must not send mail")

    class Recipients:
        def resolve(self, recipient_ref):
            return "reader@example.com"

    root = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        root,
        load_workspace_migrations(with_runtime=True),
    ).initialize()
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_mail=True,
                mail_sender=Sender(),
                recipient_resolver=Recipients(),
            )
        ],
        auto_bind=False,
    )

    assert injector.get(DispatchDigestPort) is not None


def _generation_budget() -> GenerationBudgetPolicy:
    return GenerationBudgetPolicy(
        "2026-09",
        "USD",
        1_000_000,
        10_000,
        "a" * 64,
    )


class _NoCallStructuredGenerator:
    def __call__(self, request):
        del request
        raise AssertionError("composition must not execute the model")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"structured_generation": _NoCallStructuredGenerator()},
        {"generation_budget_policy": _generation_budget()},
    ],
)
def test_worker_tracked_explanation_requires_complete_runtime_pair(
    tmp_path: Path,
    kwargs,
) -> None:
    root = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        root,
        load_workspace_migrations(with_runtime=True),
    ).initialize()

    with pytest.raises(
        ValueError,
        match="invalid_explanation_runtime_configuration",
    ):
        Injector([WorkerCliModule(str(root), **kwargs)], auto_bind=False)


def test_worker_rejects_custom_explanation_plus_tracked_runtime(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        root,
        load_workspace_migrations(with_runtime=True),
    ).initialize()

    with pytest.raises(
        ValueError,
        match="invalid_explanation_runtime_configuration",
    ):
        Injector(
            [
                WorkerCliModule(
                    str(root),
                    explanation=object(),
                    structured_generation=_NoCallStructuredGenerator(),
                    generation_budget_policy=_generation_budget(),
                )
            ],
            auto_bind=False,
        )


def test_worker_complete_tracked_runtime_binds_explanation_port_without_model_call(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        root,
        load_workspace_migrations(with_runtime=True),
    ).initialize()
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                structured_generation=_NoCallStructuredGenerator(),
                generation_budget_policy=_generation_budget(),
            )
        ],
        auto_bind=False,
    )

    assert injector.get(ProcessEvidenceExplanationPort) is not None


def test_operational_health_composition_reads_empty_runtime_as_real_zeroes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    info = SqliteWorkspaceBootstrapAdapter(
        root,
        load_workspace_migrations(with_runtime=True),
    ).initialize()
    assert info.schema_version == 25
    assert info.external_effects_enabled is False

    injector = Injector(
        [OperationalHealthCliModule(str(root))],
        auto_bind=False,
    )
    report = injector.get(InspectHealthPort)()

    assert report.sources == ()
    assert report.explanations is not None
    assert report.explanations.qa_rejected == 0
    assert report.explanations.usage_periods == ()
    assert report.delivery is not None
    assert report.delivery.unknown_deliveries == 0
