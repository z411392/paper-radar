import json

import pytest
from injector import Injector, UnsatisfiedRequirement

from apps.cli.adapters.driving.show_version import show_version
from apps.cli.module import CliModule
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
